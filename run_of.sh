#!/bin/bash
# Get username and write the CFD script to WSL home
USERNAME=$(whoami)
echo "User: $USERNAME, Home: $HOME"

# Write the CFD run script to WSL's internal filesystem
cat > $HOME/run_cfd.sh << 'SCRIPT_EOF'
#!/bin/bash
LOG=$HOME/run_cfd.log
> $LOG
exec 2>&1
exec > >(tee -a $LOG)

# Source OpenFOAM
source /usr/share/openfoam/etc/bashrc

CASE=/mnt/e/UrbanWind/cfd_cases/nju_xianlin
cd $CASE

echo "========================================="
echo "UrbanWind CFD Pipeline"
echo "Case: $CASE"
echo "Started: $(date)"
echo "========================================="

echo ""
echo "=== [1/4] blockMesh ==="
blockMesh
echo "blockMesh exit: $?"

echo ""
echo "=== [2/4] snappyHexMesh -overwrite ==="
snappyHexMesh -overwrite
echo "snappyHexMesh exit: $?"

echo ""
echo "=== [3/4] checkMesh ==="
checkMesh

echo ""
echo "=== [4/4] simpleFoam ==="
echo "Start solve: $(date)"
simpleFoam
echo "simpleFoam exit: $?"

echo ""
echo "=== PIPELINE DONE: $(date) ==="
SCRIPT_EOF

chmod +x $HOME/run_cfd.sh
echo "Script written to $HOME/run_cfd.sh"
# Run it
cd $HOME
exec bash -l $HOME/run_cfd.sh
