#!/bin/bash
# Write progress check to WSL internal fs, then run it
cat > /root/check.sh << 'EOF'
#!/bin/bash
echo "=== Processes ==="
ps aux | grep -E "snappy|simple|block" | grep -v grep
echo "=== Log tail ==="
tail -20 /root/run_cfd.log 2>/dev/null
echo "=== Milestones ==="
grep -E "DONE|FATAL|Morphing|Start solve" /root/run_cfd.log 2>/dev/null
EOF
chmod +x /root/check.sh
bash -l /root/check.sh
