#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────
# deploy-aws.sh — Deploy nanda-context-graph to AWS EC2
#
# Prerequisites:
#   - AWS CLI configured (aws configure)
#   - An SSH key pair in AWS (default: ncg-key)
#   - Permissions: ec2:*, security-group:*
#
# Usage:
#   chmod +x scripts/deploy-aws.sh
#   ./scripts/deploy-aws.sh              # uses defaults
#   ./scripts/deploy-aws.sh --key mykey  # custom key pair
#
# What it does:
#   1. Creates a security group (ncg-sg) opening ports 8080, 22
#   2. Launches an Ubuntu 22.04 t3.medium instance
#   3. SSHs in, installs Docker, clones the repo, starts services
#   4. Prints the public URL for the dashboard
# ──────────────────────────────────────────────────────────────
set -euo pipefail

# ── Configuration ────────────────────────────────────────────
INSTANCE_TYPE="${NCG_INSTANCE_TYPE:-t3.medium}"
KEY_NAME="${NCG_KEY_NAME:-ncg-key}"
REGION="${AWS_DEFAULT_REGION:-us-east-1}"
SECURITY_GROUP="ncg-sg"
REPO_URL="https://github.com/Ngoga-Musagi/nanda-context-graph.git"
TAG_NAME="nanda-context-graph"

# Parse args
while [[ $# -gt 0 ]]; do
  case $1 in
    --key)       KEY_NAME="$2"; shift 2 ;;
    --region)    REGION="$2"; shift 2 ;;
    --instance)  INSTANCE_TYPE="$2"; shift 2 ;;
    *)           echo "Unknown arg: $1"; exit 1 ;;
  esac
done

echo "=== NCG AWS Deployment ==="
echo "Region:   $REGION"
echo "Instance: $INSTANCE_TYPE"
echo "Key:      $KEY_NAME"
echo ""

# ── Get latest Ubuntu 22.04 AMI ─────────────────────────────
echo ">> Finding Ubuntu 22.04 AMI..."
AMI_ID=$(aws ec2 describe-images \
  --region "$REGION" \
  --owners 099720109477 \
  --filters "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" \
            "Name=state,Values=available" \
  --query 'Images | sort_by(@, &CreationDate) | [-1].ImageId' \
  --output text)
echo "   AMI: $AMI_ID"

# ── Create security group ───────────────────────────────────
echo ">> Creating security group..."
VPC_ID=$(aws ec2 describe-vpcs --region "$REGION" \
  --filters "Name=is-default,Values=true" \
  --query 'Vpcs[0].VpcId' --output text)

SG_ID=$(aws ec2 describe-security-groups --region "$REGION" \
  --filters "Name=group-name,Values=$SECURITY_GROUP" "Name=vpc-id,Values=$VPC_ID" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || echo "None")

if [ "$SG_ID" = "None" ] || [ -z "$SG_ID" ]; then
  SG_ID=$(aws ec2 create-security-group \
    --region "$REGION" \
    --group-name "$SECURITY_GROUP" \
    --description "nanda-context-graph ports" \
    --vpc-id "$VPC_ID" \
    --query 'GroupId' --output text)

  # SSH
  aws ec2 authorize-security-group-ingress --region "$REGION" \
    --group-id "$SG_ID" --protocol tcp --port 22 --cidr 0.0.0.0/0
  # Dashboard
  aws ec2 authorize-security-group-ingress --region "$REGION" \
    --group-id "$SG_ID" --protocol tcp --port 8080 --cidr 0.0.0.0/0
  # Ingest API (for remote agents)
  aws ec2 authorize-security-group-ingress --region "$REGION" \
    --group-id "$SG_ID" --protocol tcp --port 7200 --cidr 0.0.0.0/0
  # Query API (direct access)
  aws ec2 authorize-security-group-ingress --region "$REGION" \
    --group-id "$SG_ID" --protocol tcp --port 7201 --cidr 0.0.0.0/0
fi
echo "   Security group: $SG_ID"

# ── User-data script (runs on first boot) ───────────────────
USER_DATA=$(cat <<'USERDATA'
#!/bin/bash
set -ex

# Install Docker
curl -fsSL https://get.docker.com | sh
systemctl enable docker
usermod -aG docker ubuntu

# Install Docker Compose
apt-get install -y docker-compose-plugin

# Clone and start
cd /home/ubuntu
sudo -u ubuntu git clone REPO_PLACEHOLDER nanda-context-graph
cd nanda-context-graph
docker compose up -d --build

echo "NCG deployment complete" > /home/ubuntu/deploy.log
USERDATA
)
USER_DATA="${USER_DATA//REPO_PLACEHOLDER/$REPO_URL}"

# ── Launch instance ──────────────────────────────────────────
echo ">> Launching EC2 instance..."
INSTANCE_ID=$(aws ec2 run-instances \
  --region "$REGION" \
  --image-id "$AMI_ID" \
  --instance-type "$INSTANCE_TYPE" \
  --key-name "$KEY_NAME" \
  --security-group-ids "$SG_ID" \
  --user-data "$USER_DATA" \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":30,"VolumeType":"gp3"}}]' \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$TAG_NAME}]" \
  --query 'Instances[0].InstanceId' \
  --output text)
echo "   Instance: $INSTANCE_ID"

# ── Wait for public IP ──────────────────────────────────────
echo ">> Waiting for public IP..."
aws ec2 wait instance-running --region "$REGION" --instance-ids "$INSTANCE_ID"
PUBLIC_IP=$(aws ec2 describe-instances \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' \
  --output text)

echo ""
echo "========================================"
echo "  NCG deployed to AWS!"
echo "========================================"
echo ""
echo "  Dashboard:  http://$PUBLIC_IP:8080"
echo "  Query API:  http://$PUBLIC_IP:7201"
echo "  Ingest API: http://$PUBLIC_IP:7200"
echo "  Neo4j:      http://$PUBLIC_IP:7474"
echo ""
echo "  SSH:  ssh -i ~/.ssh/${KEY_NAME}.pem ubuntu@$PUBLIC_IP"
echo ""
echo "  NOTE: Services take ~2-3 minutes to start after boot."
echo "        Check progress: ssh in, then: tail -f /var/log/cloud-init-output.log"
echo ""
echo "  To tear down:"
echo "    aws ec2 terminate-instances --instance-ids $INSTANCE_ID --region $REGION"
echo "========================================"
