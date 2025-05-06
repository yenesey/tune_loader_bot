#!/bin/bash

COLOR_RED="$(printf '\033[1;31m')"
COLOR_GREEN="$(printf '\033[1;32m')"
COLOR_DEFAULT="$(printf '\033[0m')"

if [ "$EUID" -ne 0 ]; then
    echo "${COLOR_RED}ERROR${COLOR_DEFAULT}: Please run this script as root"
    exit 1
fi

# Check if service is running
SERVICE_NAME="tune-loader-bot.service"
echo "Checking if the service '${SERVICE_NAME}' is running ..."
if systemctl is-active --quiet "${SERVICE_NAME}"; then
    echo "Service '${SERVICE_NAME}' is running"

    # Stop and unload the service
    if systemctl stop "${SERVICE_NAME}"; then
        echo "Service '${SERVICE_NAME}' stopped"
    else
        echo "${COLOR_RED}ERROR${COLOR_DEFAULT}: The service '${SERVICE_NAME}' Can not be stopped"
        exit 1
    fi

else
    echo "Service '${SERVICE_NAME}' is not running"
fi

WORK_DIR="$(dirname "$(readlink -f "$0")")"
echo "Bot will be installed in '${WORK_DIR}'"
if ! cd "${WORK_DIR}"; then
    echo "${COLOR_RED}ERROR${COLOR_DEFAULT}: Can not cd into '${WORK_DIR}' folder"
    exit 1
fi

USER_NAME="$(stat -c "%U" .)"
if [ "${USER_NAME}" == "root" ] || [ "${USER_NAME}" == "UNKNOWN" ] ; then
    echo "${COLOR_RED}ERROR${COLOR_DEFAULT}: The owner of Bot directory is '${USER_NAME}'."
    echo "Please, change the owner with the command 'chown <user>:<user> -R \"${WORK_DIR}\"'"
    echo "The user <user> will be used to run Bot."
    exit 1
fi
echo "Service will be executed with the user '${USER_NAME}'"

# Write the systemd service descriptor
SERVICE_NAME_PATH="/etc/systemd/system/${SERVICE_NAME}"
echo "Creating unit file in '${SERVICE_NAME_PATH}' ..."
cat > "${SERVICE_NAME_PATH}" <<EOL
[Unit]
Description=Torrent Manager Bot Daemon
After=network.target

[Service]
SyslogIdentifier=torrent-manager-bot
StandardOutput=file:/var/log/tune-loader-bot.log
StandardError=file:/var/log/tune-loader-bot.log
Restart=always
RestartSec=5
Type=simple
User=${USER_NAME}
Group=users
WorkingDirectory=${WORK_DIR}

ExecStart=/bin/bash -c "source .venv/bin/activate;python bot.py"

TimeoutStopSec=30

[Install]
WantedBy=multi-user.target

EOL
if [ $? -ne 0 ]; then
    echo "${COLOR_RED}ERROR${COLOR_DEFAULT}: Can not create the file '${SERVICE_NAME_PATH}'"
    echo "The UnitPath of systemd changes from one distribution to another. You may have to edit the script and change the path manually."
    exit 1
fi

echo "Installing service ..."
# Reload systemd daemon
if ! systemctl daemon-reload; then
    echo "${COLOR_RED}ERROR${COLOR_DEFAULT}: Can not reload systemd daemon"
    exit 1
fi

# Enable the service for following restarts
if ! systemctl enable "${SERVICE_NAME}"; then
    echo "${COLOR_RED}ERROR${COLOR_DEFAULT}: Can not enable the service '${SERVICE_NAME}'"
    exit 1
fi

# Run the service
if systemctl start "${SERVICE_NAME}"; then
    echo "${COLOR_GREEN}Service successfully installed and launched!${COLOR_DEFAULT}"
else
    echo "${COLOR_RED}ERROR${COLOR_DEFAULT}: Can not start the service '${SERVICE_NAME}'"
    exit 1
fi
