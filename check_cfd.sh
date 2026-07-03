#!/bin/bash
# Bootstrap: write check script to WSL internal fs, then exec it
cat > /root/check_cfd.sh << 'XEOF'
#!/bin/bash
echo "=== Processes ==="
ps aux 2>/dev/null | grep -E "snappy|simple|block" | grep -v grep
echo "=== Log size ==="
wc -l /root/run_cfd.log 2>/dev/null
echo "=== Milestones ==="
grep -E "DONE|FATAL|Morphing|Start solve|PIPELINE DONE" /root/run_cfd.log 2>/dev/null
echo "=== Tail ==="
tail -5 /root/run_cfd.log 2>/dev/null
XEOF
chmod +x /root/check_cfd.sh
bash -l /root/check_cfd.sh
