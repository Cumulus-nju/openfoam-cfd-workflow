"""
llama.cpp inference engine wrapper.

Uses llama-server.exe (OpenAI-compatible HTTP API) for inference.
The server is managed as a background process started on first use.

Dual-backend architecture:
1. llama.cpp HTTP server (primary) — via llama-server.exe, OpenAI API compatible
2. llama-cpp-python (fallback) — Python bindings if available
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError

from ..config import (
    MODEL_FILE, LLM_MAX_TOKENS, LLM_TEMPERATURE, LLM_TOP_P,
    LLM_CONTEXT_LENGTH, LLM_N_THREADS, MODELS_DIR,
)

logger = logging.getLogger(__name__)

# llama-server default port
LLAMA_SERVER_PORT = 8081
LLAMA_SERVER_URL = f"http://127.0.0.1:{LLAMA_SERVER_PORT}"


class LLMNotReadyError(Exception):
    """Raised when the LLM model isn't loaded yet."""
    pass


def _find_llama_server() -> Optional[Path]:
    """Find the llama-server executable."""
    bin_dir = MODELS_DIR / "llama_bin"
    for candidate in [
        bin_dir / "llama-server.exe",
        bin_dir / "llama-server",
    ]:
        if candidate.exists():
            return candidate
    return None


def _find_llama_cli() -> Optional[Path]:
    """Find the llama-cli executable (fallback)."""
    bin_dir = MODELS_DIR / "llama_bin"
    for candidate in [
        bin_dir / "llama-cli.exe",
        bin_dir / "llama-cli",
    ]:
        if candidate.exists():
            return candidate
    return None


# ═══════════════════════════════════════════════════════════════════════════════
# LLM Engine
# ═══════════════════════════════════════════════════════════════════════════════


class LLMEngine:
    """
    Singleton LLM engine that manages a llama-server background process.

    Usage:
        engine = LLMEngine.get_instance()
        engine.ensure_loaded()
        result = engine.chat([{"role": "user", "content": "..."}])
    """

    _instance: Optional["LLMEngine"] = None
    _lock = threading.Lock()

    def __init__(self):
        self._server_process: Optional[subprocess.Popen] = None
        self._model = None            # llama-cpp-python fallback
        self._backend = None          # "server", "cli", or "python"
        self._loaded = False
        self._loading = False

    @classmethod
    def get_instance(cls) -> "LLMEngine":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @property
    def is_loaded(self) -> bool:
        if self._backend == "server":
            return self._server_healthy()
        return self._loaded

    @property
    def backend_name(self) -> Optional[str]:
        return self._backend

    @property
    def is_available(self) -> bool:
        """Check if model file + at least one backend exist."""
        if not MODEL_FILE.exists():
            return False
        return _find_llama_server() is not None or _find_llama_cli() is not None

    # ── Memory safety ────────────────────────────────────────────────────

    # Minimum free memory required before loading (MB)
    MIN_FREE_RAM_MB = 2048          # Hard limit — system must have this much
    MIN_FREE_VRAM_MB = 384          # Soft limit — if VRAM is low, use CPU fallback

    def _check_memory(self) -> tuple[str, bool, str]:
        """
        Check free RAM and VRAM. Returns (mode, ok, message).
        mode: "gpu" = all good, "cpu" = GPU unavailable but CPU OK, "fail" = nothing works
        """
        free_vram_mb = float("inf")
        free_ram_mb = float("inf")
        ram_ok = True
        vram_ok = True

        # ── System RAM check (HARD) ───────────────────────────────────
        try:
            import ctypes
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]
            mem = MEMORYSTATUSEX()
            mem.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(mem))
            free_ram_mb = mem.ullAvailPhys / (1024 * 1024)
            if free_ram_mb < self.MIN_FREE_RAM_MB:
                ram_ok = False
            else:
                logger.info(f"内存检查通过: {free_ram_mb:.0f} MB 可用")
        except Exception as e:
            logger.debug(f"无法检查系统内存: {e}")

        # ── VRAM check (SOFT) ─────────────────────────────────────────
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                free_vram_mb = float(result.stdout.strip().split("\n")[0])
                if free_vram_mb < self.MIN_FREE_VRAM_MB:
                    vram_ok = False
                else:
                    logger.info(f"显存检查通过: {free_vram_mb:.0f} MB 可用")
        except Exception as e:
            logger.debug(f"无法检查VRAM: {e}")

        # ── Decision ──────────────────────────────────────────────────
        if not ram_ok and not vram_ok:
            return ("fail", False,
                    f"可用内存 {free_ram_mb:.0f} MB + 显存 {free_vram_mb:.0f} MB 均不足，"
                    f"无法加载模型。请关闭部分应用后重试。")
        if not ram_ok:
            return ("fail", False,
                    f"可用内存不足: {free_ram_mb:.0f} MB < {self.MIN_FREE_RAM_MB} MB (安全阈值)")
        if not vram_ok:
            logger.warning(f"显存不足 ({free_vram_mb:.0f} MB)，自动降级为 CPU 推理")
            return ("cpu", True,
                    f"显存不足 ({free_vram_mb:.0f} MB < {self.MIN_FREE_VRAM_MB} MB)，"
                    f"已切换为 CPU 推理，速度较慢但不影响使用。")
        return ("gpu", True, "OK")

    # ── Lifecycle ────────────────────────────────────────────────────────

    def ensure_loaded(self, model_path: Optional[Path] = None) -> bool:
        """
        Start the llama-server and load the model. Returns True on success.
        Auto-detects best backend: llama-server > llama-cpp-python > llama-cli.
        """
        if self._loaded or (self._backend == "server" and self._server_healthy()):
            return True

        path = model_path or MODEL_FILE
        if not path.exists():
            logger.warning(f"Model file not found: {path}")
            return False

        # ── Memory safety gate ───────────────────────────────────────
        mode, ok, msg = self._check_memory()
        if not ok:
            logger.error(f"内存安全检查未通过: {msg}")
            logger.error("拒绝加载模型以避免系统卡死。请关闭部分应用后重试。")
            return False

        use_gpu = (mode == "gpu")

        with self._lock:
            if self._loaded or (self._backend == "server" and self._server_healthy()):
                return True
            if self._loading:
                return False
            self._loading = True

            try:
                # 1. Try GPU server (only if VRAM is sufficient)
                if use_gpu:
                    server_exe = _find_llama_server()
                    if server_exe:
                        ok = self._start_server(server_exe, path)
                        if ok:
                            self._backend = "server"
                            self._loaded = True
                            logger.info("LLM ready via llama-server (GPU)")
                            return True
                    logger.info("GPU server unavailable, falling back to CPU...")
                else:
                    logger.info("VRAM不足，跳过GPU，直接使用CPU推理...")

                # 2. Try llama-cpp-python (CPU)
                try:
                    from llama_cpp import Llama
                    self._model = Llama(
                        model_path=str(path),
                        n_ctx=LLM_CONTEXT_LENGTH,
                        n_threads=LLM_N_THREADS,
                        verbose=False,
                    )
                    self._backend = "python"
                    self._loaded = True
                    logger.info("LLM ready via llama-cpp-python")
                    return True
                except Exception as e:
                    logger.debug(f"Python backend not available: {e}")

                # 3. Fall back to llama-cli subprocess
                cli_exe = _find_llama_cli()
                if cli_exe:
                    self._model = cli_exe  # Store CLI path
                    self._backend = "cli"
                    self._loaded = True
                    logger.info(f"LLM ready via CLI fallback: {cli_exe}")
                    return True

                logger.error("No functional LLM backend found")
                return False
            finally:
                self._loading = False

    def unload(self):
        """Stop the server and free resources."""
        self._stop_server()
        self._model = None
        self._loaded = False
        self._loading = False
        self._backend = None

    # ── Server management ────────────────────────────────────────────────

    def _start_server(self, exe: Path, model_path: Path) -> bool:
        """Start llama-server as a background process."""
        cmd = [
            str(exe),
            "-m", str(model_path),
            "--host", "127.0.0.1",
            "--port", str(LLAMA_SERVER_PORT),
            "-c", str(LLM_CONTEXT_LENGTH),
            "-np", "1",  # Single parallel slot
            "-fa",        # Flash attention
            "-ngl", "99", # Offload all layers to GPU
        ]

        try:
            logger.info(f"Starting llama-server: {' '.join(cmd)}")
            self._server_process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=str(MODELS_DIR),
            )

            # Wait for server to be ready (up to 60s)
            for i in range(60):
                time.sleep(1)
                if self._server_healthy():
                    logger.info(f"llama-server ready after {i+1}s")
                    return True
                if self._server_process.poll() is not None:
                    logger.error("llama-server exited unexpectedly")
                    return False

            logger.error("llama-server did not become ready within 60s")
            self._stop_server()
            return False

        except Exception as e:
            logger.error(f"Failed to start llama-server: {e}")
            return False

    def _stop_server(self):
        """Stop the llama-server process."""
        if self._server_process:
            try:
                self._server_process.terminate()
                self._server_process.wait(timeout=10)
            except Exception:
                try:
                    self._server_process.kill()
                except Exception:
                    pass
            self._server_process = None

    def _server_healthy(self) -> bool:
        """Check if llama-server is responding."""
        try:
            req = Request(f"{LLAMA_SERVER_URL}/health", method="GET")
            with urlopen(req, timeout=3) as resp:
                return resp.status == 200
        except Exception:
            return False

    # ── Inference ────────────────────────────────────────────────────────

    def chat(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = LLM_MAX_TOKENS,
        temperature: float = LLM_TEMPERATURE,
        top_p: float = LLM_TOP_P,
        response_format: Optional[str] = None,
    ) -> str:
        """Non-streaming chat completion."""
        if not self._loaded and not self._server_healthy():
            raise LLMNotReadyError("Model not loaded. Call ensure_loaded() first.")

        if self._backend == "server":
            return self._chat_server(messages, max_tokens, temperature, top_p, response_format, stream=False)
        elif self._backend == "python":
            return self._chat_python(messages, max_tokens, temperature, top_p, response_format)
        else:
            return self._chat_cli(messages, max_tokens, temperature, top_p, response_format)

    def chat_stream(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = LLM_MAX_TOKENS,
        temperature: float = LLM_TEMPERATURE,
        top_p: float = LLM_TOP_P,
    ) -> Generator[str, None, None]:
        """Streaming chat completion. Yields text tokens."""
        if not self._loaded and not self._server_healthy():
            raise LLMNotReadyError("Model not loaded.")

        if self._backend == "server":
            yield from self._chat_server_stream(messages, max_tokens, temperature, top_p)
        elif self._backend == "python":
            yield from self._chat_python_stream(messages, max_tokens, temperature, top_p)
        else:
            # CLI backend: just yield the full response
            yield self._chat_cli(messages, max_tokens, temperature, top_p)

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        max_tokens: int = LLM_MAX_TOKENS,
        retries: int = 2,
    ) -> Dict[str, Any]:
        """Chat with guaranteed JSON response. Retries on parse failure."""
        for attempt in range(retries + 1):
            text = self.chat(
                messages=messages,
                max_tokens=max_tokens,
                temperature=LLM_TEMPERATURE,
                response_format="json_object",
            )
            try:
                return json.loads(text)
            except json.JSONDecodeError as e:
                if attempt < retries:
                    messages.append({
                        "role": "user",
                        "content": f"响应不是有效JSON ({e})。请只返回纯JSON，不要markdown包裹，不要解释。",
                    })
                else:
                    logger.warning(f"JSON parse failed after {retries} retries")
                    return {"error": "json_parse_failed", "raw_text": text[:500]}
        return {"error": "unreachable"}

    # ── HTTP server backend ──────────────────────────────────────────────

    def _chat_server(self, messages, max_tokens, temperature, top_p, response_format, stream=False):
        """Call llama-server's /v1/chat/completions endpoint."""
        body = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "stream": stream,
        }
        if response_format == "json_object":
            body["response_format"] = {"type": "json_object"}

        data = json.dumps(body).encode("utf-8")
        req = Request(
            f"{LLAMA_SERVER_URL}/v1/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(req, timeout=120) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                text = result["choices"][0]["message"]["content"].strip()
                if response_format == "json_object":
                    text = self._extract_json(text)
                return text
        except URLError as e:
            raise LLMNotReadyError(f"llama-server request failed: {e}")

    def _chat_server_stream(self, messages, max_tokens, temperature, top_p):
        """Stream from llama-server. Yields text tokens."""
        body = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "stream": True,
        }
        data = json.dumps(body).encode("utf-8")
        req = Request(
            f"{LLAMA_SERVER_URL}/v1/chat/completions",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urlopen(req, timeout=120) as resp:
                for line in resp:
                    line = line.decode("utf-8").strip()
                    if line.startswith("data: ") and line != "data: [DONE]":
                        try:
                            chunk = json.loads(line[6:])
                            delta = chunk["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        except json.JSONDecodeError:
                            continue
        except URLError as e:
            raise LLMNotReadyError(f"llama-server stream failed: {e}")

    # ── Python backend ───────────────────────────────────────────────────

    def _chat_python(self, messages, max_tokens, temperature, top_p, response_format):
        prompt = self._build_qwen_prompt(messages, json_mode=(response_format == "json_object"))
        result = self._model.create_completion(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            stop=["</s>", "<|im_end|>", "<|endoftext|>"],
            echo=False,
        )
        text = result["choices"][0]["text"].strip()
        if response_format == "json_object":
            text = self._extract_json(text)
        return text

    def _chat_python_stream(self, messages, max_tokens, temperature, top_p):
        prompt = self._build_qwen_prompt(messages)
        stream = self._model.create_completion(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            stop=["</s>", "<|im_end|>", "<|endoftext|>"],
            echo=False,
            stream=True,
        )
        for chunk in stream:
            text = chunk["choices"][0].get("text", "")
            if text:
                yield text

    # ── CLI fallback backend ─────────────────────────────────────────────

    def _chat_cli(self, messages, max_tokens, temperature, top_p, response_format):
        """Run inference via llama-cli subprocess with prompt file."""
        cli_exe = self._model  # Stored as Path to llama-cli.exe
        if not isinstance(cli_exe, Path):
            raise LLMNotReadyError("CLI backend not configured")

        prompt = self._build_qwen_prompt(messages, json_mode=(response_format == "json_object"))

        # Write prompt to file to preserve encoding
        prompt_file = MODELS_DIR / "_temp_prompt.txt"
        prompt_file.write_text(prompt, encoding="utf-8")

        cmd = [
            str(cli_exe),
            "-m", str(MODEL_FILE),
            "-f", str(prompt_file),
            "-n", str(max_tokens),
            "--temp", str(temperature),
            "--top-p", str(top_p),
            "--threads", str(LLM_N_THREADS),
            "-c", str(LLM_CONTEXT_LENGTH),
            "--no-display-prompt",
            "-ngl", "99",
            "--simple-io",
            "-e",
        ]

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(MODELS_DIR),
            )
            text = result.stdout.strip()
            if not text and result.stderr:
                text = result.stderr.strip()
            if response_format == "json_object":
                text = self._extract_json(text)
            return text
        except subprocess.TimeoutExpired:
            return '{"error": "timeout"}'
        finally:
            if prompt_file.exists():
                prompt_file.unlink()

    # ── Helpers ──────────────────────────────────────────────────────────

    def _build_qwen_prompt(self, messages: List[Dict[str, str]], json_mode: bool = False) -> str:
        """Build Qwen2.5 chat format: <|im_start|>role\ncontent<|im_end|>"""
        parts = []
        for msg in messages:
            parts.append(f"<|im_start|>{msg['role']}\n{msg['content']}<|im_end|>")
        if json_mode:
            parts.append("<|im_start|>assistant\n")
        else:
            parts.append("<|im_start|>assistant\n")
        return "\n".join(parts)

    def _extract_json(self, text: str) -> str:
        """Extract JSON from text (may contain markdown fences)."""
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        elif text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        return text.strip()


# ── Module-level convenience ──

def get_engine() -> LLMEngine:
    """Get the global LLM engine instance."""
    return LLMEngine.get_instance()
