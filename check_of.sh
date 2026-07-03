#!/bin/bash
echo "=== checking OF ==="
ls -la /usr/bin/blockMesh 2>&1
ls -la /usr/bin/simpleFoam 2>&1
which blockMesh 2>&1
dpkg -l 2>/dev/null | grep -i openfoam
echo "=== apt search ==="
apt-cache search openfoam 2>/dev/null | head -5
echo "=== done ==="
