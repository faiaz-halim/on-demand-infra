# On-Demand Infrastructure

A CLI tool and API for generating and deploying infrastructure as code (IaC) based on natural language prompts. Supports local (Kind), cloud-local (EC2 + Kind), and cloud-hosted (EKS) deployment modes.

## Features

- **OpenAI-Compatible API**: `/v1/chat/completions` endpoint for infrastructure generation
- **Multi-Deployment Modes**:
  - Local (Kind cluster)
  - Cloud-Local (EC2 instance with Kind)
  - Cloud-Hosted (AWS EKS)
- **GitHub Integration**: Automatic analysis of repositories for Dockerfiles and build commands
- **Security**: Input validation and sanitization, Terraform and Kubernetes security scanning
- **Extensible**: MCP server integration for context-aware code generation

## Prerequisites

- Python 3.9+
- Docker
- Terraform
- kubectl
- AWS CLI (for cloud deployments)
- Kind (for local deployments)

## Installation

```bash
# Clone the repository
git clone https://github.com/faiaz-halim/on-demand-infra.git
cd on-demand-infra-scaffold

# Create virtual environment
python -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r code/requirements.txt
```

## Configuration

1. Rename `.env.example` to `.env` and fill in your Azure OpenAI credentials:
```ini
AZURE_OPENAI_API_KEY=your_azure_openai_api_key_here
AZURE_OPENAI_ENDPOINT=https://your-resource-name.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT_NAME=your-deployment-name
```

2. For AWS deployments, configure your credentials:
```bash
aws configure
```

## Usage

### CLI Interface
```bash
# Local deployment
python code/cli.py deploy-local --app-name myapp --image myapp:latest

# Cloud-hosted deployment
python code/cli.py deploy-cloud-hosted --app-name myapp --image myapp:latest --cluster-name mycluster
```

### API Server
```bash
uvicorn code.main:app --reload --port 8000
```

Send requests to the API:
```bash
curl -X POST "http://localhost:8000/v1/chat/completions" \
-H "Content-Type: application/json" \
-d '{
  "prompt": "Deploy a Node.js app from https://github.com/example/node-app in cloud-hosted mode",
  "github_url": "https://github.com/faiaz-halim/on-demand-test-repo.git",
  "deployment_mode": "cloud-hosted"
}'
```

## Security

Security measures include:
- Input sanitization for shell commands and Kubernetes/Terraform inputs
- Static analysis with tfsec` and kube-linter
- Least privilege IAM roles

Run security scans:
```bash
# Run tfsec
./code/run_tfsec.sh

# Run kube-linter
kube-linter lint path/to/manifests/
```

## Contributing

1. Fork the repository
2. Create a new branch (`git checkout -b feature/your-feature`)
3. Commit your changes (`git commit -am 'Add some feature'`)
4. Push to the branch (`git push origin feature/your-feature`)
5. Open a pull request

## License

MIT License. See [LICENSE](LICENSE) for details.
