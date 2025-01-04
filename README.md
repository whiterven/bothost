Here's how to deploy the bot on AWS for continuous operation:

Launch an EC2 instance:


Choose Amazon Linux 2023
t2.micro for basic usage
Configure security group to allow inbound SSH (port 22)


Connect and setup:
bashCopysudo yum update -y
sudo yum install python3-pip git screen -y
git clone [your-repository]
cd [repository-name]
pip3 install -r requirements.txt

Create environment file:

bashCopynano .env
# Add all environment variables

Create a systemd service for auto-restart:

bashCopysudo nano /etc/systemd/system/telegram-bot.service
iniCopy[Unit]
Description=Telegram Bot Service
After=network.target

[Service]
Type=simple
User=ec2-user
WorkingDirectory=/home/ec2-user/[repository-name]
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 main.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target

Start the service:

bashCopysudo systemctl daemon-reload
sudo systemctl enable telegram-bot
sudo systemctl start telegram-bot

Monitor logs:

bashCopysudo journalctl -u telegram-bot -f
Optional: Set up CloudWatch for monitoring and alerts on service failures.
