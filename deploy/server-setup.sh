#!/usr/bin/env bash
# One-time setup for a fresh Ubuntu server (tested for Oracle Cloud Always Free, Ubuntu 24.04).
# Installs Docker, opens the web ports in the server firewall, adds swap on small machines,
# and turns on automatic security updates.
# Usage: bash deploy/server-setup.sh
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi

echo "==> Refreshing package lists"
$SUDO apt-get update -qq

echo "==> Installing Docker"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | $SUDO sh
fi
# The login user (also when the script is run with sudo or as root).
TARGET_USER="${SUDO_USER:-${USER:-$(id -un)}}"
$SUDO usermod -aG docker "$TARGET_USER" || true

echo "==> Opening ports 80 and 443"
# Oracle's Ubuntu images block everything except SSH with iptables, in addition to the
# cloud firewall (the VCN security list), so both must allow the web ports.
if command -v iptables >/dev/null 2>&1; then
  for port in 80 443; do
    $SUDO iptables -C INPUT -p tcp --dport "$port" -m state --state NEW -j ACCEPT 2>/dev/null \
      || $SUDO iptables -I INPUT 1 -p tcp --dport "$port" -m state --state NEW -j ACCEPT
  done
  $SUDO iptables -C INPUT -p udp --dport 443 -j ACCEPT 2>/dev/null \
    || $SUDO iptables -I INPUT 1 -p udp --dport 443 -j ACCEPT
  # Save the rules so they survive a reboot (iptables-persistent provides the saving plugin).
  if ! dpkg -s iptables-persistent >/dev/null 2>&1; then
    $SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent >/dev/null
  fi
  $SUDO netfilter-persistent save
fi
if command -v ufw >/dev/null 2>&1 && $SUDO ufw status | grep -q "Status: active"; then
  $SUDO ufw allow 80/tcp && $SUDO ufw allow 443/tcp && $SUDO ufw allow 443/udp
fi

echo "==> Swap (only on machines with less than 8 GB of memory)"
mem_kb=$(grep MemTotal /proc/meminfo | awk '{print $2}')
if [ "$mem_kb" -lt 8000000 ] && ! swapon --show | grep -q .; then
  if $SUDO fallocate -l 4G /swapfile && $SUDO chmod 600 /swapfile && $SUDO mkswap /swapfile && $SUDO swapon /swapfile; then
    echo "/swapfile none swap sw 0 0" | $SUDO tee -a /etc/fstab >/dev/null
  else
    echo "Could not add swap; continuing without it."
  fi
fi

echo "==> Automatic security updates"
$SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y unattended-upgrades >/dev/null
$SUDO dpkg-reconfigure -f noninteractive unattended-upgrades

echo
echo "Done. Log out and back in once so 'docker' works without sudo."
