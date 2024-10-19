#!/bin/bash

# Exit script on any error
set -e

# Variables
MINIO_USER="minio"
MINIO_GROUP="minio"
MINIO_BIN_DIR="/usr/local/bin"
MINIO_DATA_DIR="/usr/local/share/minio"
MINIO_CONFIG_DIR="/etc/minio"
MINIO_SERVICE_FILE="/etc/systemd/system/minio.service"
MINIO_CONF_FILE="$MINIO_CONFIG_DIR/minio.conf"
DOWNLOAD_METHOD="direct_url"  # Options: official_script, direct_url, github

# *** Configuration Variables ***
MINIO_ACCESS_KEY="minioadmin"
MINIO_SECRET_KEY="minioadmin"
MINIO_API_PORT="9000"
MINIO_CONSOLE_PORT="9001"

# Ensure Access Key and Secret Key are set
if [[ -z "$MINIO_ACCESS_KEY" || -z "$MINIO_SECRET_KEY" ]]; then
    echo "Error: MINIO_ACCESS_KEY and MINIO_SECRET_KEY must be set."
    exit 1
fi

# Function to check if a command exists
command_exists() {
    command -v "$@" >/dev/null 2>&1
}

# Ensure required commands are installed
REQUIRED_CMDS=("sudo" "systemctl")
for cmd in "${REQUIRED_CMDS[@]}"; do
    if ! command_exists "$cmd"; then
        echo "Error: '$cmd' is not installed."
        exit 1
    fi
done

# Determine download command
if command_exists wget; then
    DOWNLOAD_CMD="wget"
elif command_exists curl; then
    DOWNLOAD_CMD="curl"
else
    echo "Error: Neither wget nor curl is installed."
    exit 1
fi

# Function to download MinIO binary
download_minio() {
    case "$DOWNLOAD_METHOD" in
        official_script)
            echo "Downloading and installing MinIO using the official installer script..."
            if [[ "$DOWNLOAD_CMD" == "wget" ]]; then
                $DOWNLOAD_CMD -qO- https://dl.min.io/server/minio/release/linux-amd64/minio-install.sh | sudo bash
            elif [[ "$DOWNLOAD_CMD" == "curl" ]]; then
                $DOWNLOAD_CMD -sSL https://dl.min.io/server/minio/release/linux-amd64/minio-install.sh | sudo bash
            fi
            ;;
        direct_url)
            echo "Downloading MinIO binary directly from the official URL..."
            DOWNLOAD_URL="https://dl.min.io/server/minio/release/linux-amd64/minio"
            if [[ "$DOWNLOAD_CMD" == "wget" ]]; then
                $DOWNLOAD_CMD -q -O /tmp/minio "$DOWNLOAD_URL"
            elif [[ "$DOWNLOAD_CMD" == "curl" ]]; then
                $DOWNLOAD_CMD -sSL -o /tmp/minio "$DOWNLOAD_URL"
            fi
            ;;
        github)
            echo "Downloading MinIO binary from GitHub Releases..."
            DOWNLOAD_URL="https://github.com/minio/minio/releases/latest/download/minio"

            # Use correct options for wget or curl
            if [[ "$DOWNLOAD_CMD" == "wget" ]]; then
                $DOWNLOAD_CMD -q -O /tmp/minio "$DOWNLOAD_URL"
            elif [[ "$DOWNLOAD_CMD" == "curl" ]]; then
                $DOWNLOAD_CMD -sSL -o /tmp/minio "$DOWNLOAD_URL"
            else
                echo "Unsupported download command."
                exit 1
            fi
            ;;
        *)
            echo "Invalid DOWNLOAD_METHOD specified."
            exit 1
            ;;
    esac

    # Check if the binary was downloaded
    if [ ! -f /tmp/minio ]; then
        echo "Error: Failed to download MinIO binary."
        exit 1
    fi

    # Install the binary
    echo "Installing MinIO server binary..."
    sudo mv /tmp/minio "$MINIO_BIN_DIR/minio"
    sudo chmod +x "$MINIO_BIN_DIR/minio"
}

# Create minio user and group if they don't exist
if ! id -u "$MINIO_USER" >/dev/null 2>&1; then
    echo "Creating user and group '$MINIO_USER'..."
    sudo groupadd "$MINIO_GROUP"
    sudo useradd -r -s /sbin/nologin -g "$MINIO_GROUP" "$MINIO_USER"
else
    echo "User '$MINIO_USER' already exists."
fi

# Download and install MinIO binary
download_minio

# Create necessary directories
echo "Creating data and configuration directories..."
sudo mkdir -p "$MINIO_DATA_DIR"
sudo mkdir -p "$MINIO_CONFIG_DIR"

# Create the MinIO configuration file
echo "Creating MinIO configuration file..."
sudo tee "$MINIO_CONF_FILE" > /dev/null <<EOF
# MinIO configuration file

# Set the root user (access key)
MINIO_ROOT_USER=$MINIO_ACCESS_KEY

# Set the root password (secret key)
MINIO_ROOT_PASSWORD=$MINIO_SECRET_KEY

# Other configuration options can be added here
EOF

# Set ownership and permissions
echo "Setting ownership and permissions..."
sudo chown -R "$MINIO_USER":"$MINIO_GROUP" "$MINIO_DATA_DIR"
sudo chown -R "$MINIO_USER":"$MINIO_GROUP" "$MINIO_CONFIG_DIR"
sudo chmod 640 "$MINIO_CONF_FILE"

# Create the systemd service file
echo "Creating MinIO systemd service file..."

sudo tee "$MINIO_SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=MinIO object storage server
Documentation=https://docs.min.io
Wants=network-online.target
After=network-online.target

[Service]
User=$MINIO_USER
Group=$MINIO_GROUP
WorkingDirectory=$MINIO_DATA_DIR

EnvironmentFile=-$MINIO_CONF_FILE

ExecStart=$MINIO_BIN_DIR/minio server \\
 --address ":$MINIO_API_PORT" \\
 --console-address ":$MINIO_CONSOLE_PORT" \\
 $MINIO_DATA_DIR

Restart=always
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd daemon
echo "Reloading systemd daemon..."
sudo systemctl daemon-reload

# Enable and start the MinIO service
echo "Enabling and starting MinIO service..."
sudo systemctl enable minio
sudo systemctl start minio

echo "MinIO installation is complete."
echo "Access the MinIO Console at http://your-server-ip:$MINIO_CONSOLE_PORT"