#!/bin/bash
echo "=== which blockMesh ==="
which blockMesh
echo "=== blockMesh path ==="
ls -la $(which blockMesh) 2>/dev/null
echo "=== dpkg openfoam ==="
dpkg -l 2>/dev/null | grep -i openfoam
echo "=== /usr/lib/openfoam ==="
ls /usr/lib/openfoam/ 2>/dev/null || echo "NOT FOUND"
echo "=== /usr/share/openfoam ==="
ls /usr/share/openfoam/ 2>/dev/null || echo "NOT FOUND"
echo "=== /opt ==="
ls /opt/ 2>/dev/null || echo "NOT FOUND"
echo "=== find bashrc ==="
find /usr /opt /etc -maxdepth 5 -name "bashrc" 2>/dev/null | head -10
echo "=== find controlDict ==="
find /usr /opt /etc -maxdepth 5 -name "controlDict" 2>/dev/null | head -5
echo "=== END ==="
