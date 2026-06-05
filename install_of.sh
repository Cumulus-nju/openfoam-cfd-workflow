#!/bin/bash
set -e
echo 'Starting OpenFOAM install...' > /tmp/of_install.log
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -y >> /tmp/of_install.log 2>&1
echo 'Update done, installing...' >> /tmp/of_install.log
sudo apt-get install -y openfoam >> /tmp/of_install.log 2>&1
echo 'INSTALL_COMPLETE' >> /tmp/of_install.log
echo 'Checking...' >> /tmp/of_install.log
which simpleFoam >> /tmp/of_install.log 2>&1 || echo 'simpleFoam not found' >> /tmp/of_install.log
dpkg -l | grep openfoam >> /tmp/of_install.log 2>&1
