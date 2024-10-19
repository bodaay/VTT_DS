#!/bin/bash

# Exit on any error
set -e

# Variables
MINIO_USER="minio"
MINIO_GROUP="minio"
MINIO_BIN_DIR="/usr/local/bin"
MINIO_DATA_DIR="/usr/local/share/minio"
MINIO_CONFIG_DIR="/etc/minio"
MINIO_SERVICE_FILE="/etc/systemd/system/minio.service"

# Stop and disable the MinIO service if it exists
if systemctl list-unit-files | grep -q minio.service; then
    echo "Stopping and disabling the MinIO service..."
    sudo systemctl stop minio
    sudo systemctl disable minio
fi

# Remove the systemd service file
if [ -f "$MINIO_SERVICE_FILE" ]; then
    echo "Removing the MinIO systemd service file..."
    sudo rm -f "$MINIO_SERVICE_FILE"
    sudo systemctl daemon-reload
fi

# Remove the MinIO binary
if [ -f "$MINIO_BIN_DIR/minio" ]; then
    echo "Removing the MinIO binary..."
    sudo rm -f "$MINIO_BIN_DIR/minio"
fi

# Prompt the user before deleting data directory
if [ -d "$MINIO_DATA_DIR" ]; then
    read -p "Do you want to remove the MinIO data directory at $MINIO_DATA_DIR? This will delete all stored data. (y/N): " confirm
    if [[ "$confirm" =~ ^([yY][eE][sS]|[yY])$ ]]; then
        echo "Removing the MinIO data directory..."
        sudo rm -rf "$MINIO_DATA_DIR"
    else
        echo "Skipping removal of the data directory."
    fi
fi

# Prompt the user before deleting configuration directory
if [ -d "$MINIO_CONFIG_DIR" ]; then
    read -p "Do you want to remove the MinIO configuration directory at $MINIO_CONFIG_DIR? (y/N): " confirm
    if [[ "$confirm" =~ ^([yY][eE][sS]|[yY])$ ]]; then
        echo "Removing the MinIO configuration directory..."
        sudo rm -rf "$MINIO_CONFIG_DIR"
    else
        echo "Skipping removal of the configuration directory."
    fi
fi

# Remove the minio user and group
if id -u "$MINIO_USER" >/dev/null 2>&1; then
    echo "Removing the MinIO user and group..."
    sudo userdel -r "$MINIO_USER" 2>/dev/null || true
    sudo groupdel "$MINIO_GROUP" 2>/dev/null || true
fi

echo "MinIO has been successfully uninstalled."