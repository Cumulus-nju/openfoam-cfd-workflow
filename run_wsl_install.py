"""
Run OpenFOAM install via WSL using inline commands.
This avoids the Git Bash path translation issue.
"""
import subprocess
import os

commands = """
echo '=== OpenFOAM Install Log ===' > /tmp/of_install.log
echo "Started: $(date)" >> /tmp/of_install.log

# Backup original sources
sudo cp /etc/apt/sources.list /etc/apt/sources.list.bak 2>/dev/null || true
echo "Backup done" >> /tmp/of_install.log

# Switch to Tsinghua mirror
sudo sed -i 's|http://archive.ubuntu.com|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list
sudo sed -i 's|http://security.ubuntu.com|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list
echo "Mirror changed" >> /tmp/of_install.log

# Update
echo "Updating..." >> /tmp/of_install.log
sudo apt-get update -y >> /tmp/of_install.log 2>&1
echo "Update done" >> /tmp/of_install.log

# Install OpenFOAM
echo "Installing OpenFOAM..." >> /tmp/of_install.log
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y openfoam >> /tmp/of_install.log 2>&1
echo "Install done" >> /tmp/of_install.log

# Verify
echo "=== Verification ===" >> /tmp/of_install.log
dpkg -l | grep openfoam >> /tmp/of_install.log 2>&1
ls /usr/bin/*Foam* >> /tmp/of_install.log 2>&1 || echo "no *Foam* in /usr/bin" >> /tmp/of_install.log
ls /usr/lib/openfoam 2>/dev/null >> /tmp/of_install.log || echo "no /usr/lib/openfoam" >> /tmp/of_install.log

# Source and check
if [ -f /usr/lib/openfoam/openfoam/etc/bashrc ]; then
    . /usr/lib/openfoam/openfoam/etc/bashrc
elif [ -d /opt/openfoam* ]; then
    . /opt/openfoam*/etc/bashrc 2>/dev/null
fi
which blockMesh >> /tmp/of_install.log 2>&1 || echo "blockMesh not in PATH" >> /tmp/of_install.log
which simpleFoam >> /tmp/of_install.log 2>&1 || echo "simpleFoam not in PATH" >> /tmp/of_install.log

echo "INSTALL_COMPLETE" >> /tmp/of_install.log
echo "Finished: $(date)" >> /tmp/of_install.log
""".strip()

# Write inline script to D drive
script_path = r"D:\Phase2_CFD_ML\inline_install.sh"
with open(script_path, 'w', newline='\n', encoding='utf-8') as f:
    f.write(commands)

# Copy to WSL using wsl.exe with cat
print("Copying script to WSL...")
result = subprocess.run(
    ['C:/Windows/System32/wsl.exe', '-d', 'Ubuntu-22.04', '--',
     'bash', '-c', f'cat > /tmp/inline_install.sh << '"'"'EOF'"'"'\n{commands}\nEOF\nchmod +x /tmp/inline_install.sh\necho "Script copied to WSL"'],
    capture_output=True, text=True, timeout=30
)
print("stdout:", result.stdout)
print("stderr:", result.stderr)
print("returncode:", result.returncode)

# Now run the script in WSL
print("\nRunning install script in WSL...")
result = subprocess.run(
    ['C:/Windows/System32/wsl.exe', '-d', 'Ubuntu-22.04', '--',
     'bash', '/tmp/inline_install.sh'],
    capture_output=True, text=True, timeout=600
)
print("stdout:", result.stdout)
print("stderr:", result.stderr)
print("returncode:", result.returncode)

# Copy the log back to Windows
print("\nCopying log to D drive...")
subprocess.run(
    ['C:/Windows/System32/wsl.exe', '-d', 'Ubuntu-22.04', '--',
     'bash', '-c', 'cp /tmp/of_install.log /mnt/d/Phase2_CFD_ML/of_install.log'],
    timeout=10
)
print("Done!")
