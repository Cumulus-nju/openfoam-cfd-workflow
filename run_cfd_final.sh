#!/bin/bash
# Bootstrap: write everything to WSL internal fs and run there
# Per skill guidance: write to WSL fs first, then execute

cat > /root/run_pipeline.sh << 'PIPE_EOF'
#!/bin/bash
set -e

CASE_SRC=/mnt/e/UrbanWind/cfd_cases/nju_xianlin
CASE_WSL=/root/cases/nju_xianlin
LOG=/root/run_cfd.log
RESULT_DST=/mnt/e/UrbanWind/cfd_cases/nju_xianlin_results

# Source OpenFOAM
source /usr/share/openfoam/etc/bashrc 2>/dev/null || true

echo "=========================================" | tee $LOG
echo "UrbanWind CFD (WSL ext4)" | tee -a $LOG
echo "Started: $(date)" | tee -a $LOG
echo "=========================================" | tee -a $LOG

# Clean and copy case to WSL internal ext4 (fast I/O)
echo "Copying case to WSL ext4..." | tee -a $LOG
mkdir -p /root/cases
rm -rf $CASE_WSL
cp -r $CASE_SRC $CASE_WSL
echo "Case ready at $CASE_WSL" | tee -a $LOG
cd $CASE_WSL

echo "" | tee -a $LOG
echo "=== [1/4] blockMesh ===" | tee -a $LOG
echo "Start: $(date)" | tee -a $LOG
blockMesh 2>&1 | tee -a $LOG
echo "blockMesh DONE: $(date)" | tee -a $LOG

echo "" | tee -a $LOG
echo "=== [2/4] snappyHexMesh -overwrite ===" | tee -a $LOG
echo "Start: $(date)" | tee -a $LOG
snappyHexMesh -overwrite 2>&1 | tee -a $LOG
echo "snappyHexMesh DONE: $(date)" | tee -a $LOG

echo "" | tee -a $LOG
echo "=== [3/4] checkMesh ===" | tee -a $LOG
checkMesh 2>&1 | tee -a $LOG

echo "" | tee -a $LOG
echo "=== [4/4] simpleFoam ===" | tee -a $LOG
echo "Start solve: $(date)" | tee -a $LOG
simpleFoam 2>&1 | tee -a $LOG
echo "simpleFoam DONE: $(date)" | tee -a $LOG

echo "" | tee -a $LOG
echo "=== Copying results to E drive ===" | tee -a $LOG
mkdir -p $RESULT_DST
cp -r $CASE_WSL/* $RESULT_DST/
echo "Results copied to $RESULT_DST" | tee -a $LOG

echo "" | tee -a $LOG
echo "=========================================" | tee -a $LOG
echo " PIPELINE COMPLETE: $(date)" | tee -a $LOG
echo "=========================================" | tee -a $LOG
PIPE_EOF

chmod +x /root/run_pipeline.sh
echo "Script written to /root/run_pipeline.sh"
echo "Launching..."
exec bash -l /root/run_pipeline.sh
