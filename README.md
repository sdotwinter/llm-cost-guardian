# LLM Cost Guardian 🛡️

Track, control, and optimize your LLM spending with per-tenant attribution, circuit breakers, and real-time spending alerts.

## Features

- **Live Cost Dashboards** - Real-time visibility into LLM spending across all tenants
- **Circuit Breaker** - Automatic protection against runaway API costs
- **Per-Tenant Attribution** - Track costs by customer, team, or project
- **Spending Alerts** - Configurable alerts at 75%, 90%, and 100% budget thresholds

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Run the server
python main.py
```

The API will be available at `http://localhost:8000`

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| POST | `/tenants` | Create a new tenant |
| GET | `/tenants/{id}` | Get tenant details |
| POST | `/tenants/{id}/costs` | Record a cost |
| GET | `/tenants/{id}/spending` | Get tenant spending |
| GET | `/tenants/{id}/costs` | Get cost history |
| GET | `/circuit-breaker/{id}` | Get circuit breaker state |
| POST | `/circuit-breaker/{id}/reset` | Reset circuit breaker |
| GET | `/alerts` | Get spending alerts |
| GET | `/dashboard` | Get live dashboard |
| GET | `/health` | Health check |

## Example Usage

### Create a Tenant

```bash
curl -X POST http://localhost:8000/tenants \
  -H "Content-Type: application/json" \
  -d '{"name": "Acme Corp", "budget_limit": 500.0}'
```

### Record a Cost

```bash
curl -X POST http://localhost:8000/tenants/1/costs \
  -H "Content-Type: application/json" \
  -d '{"model_name": "gpt-4", "input_tokens": 1000, "output_tokens": 500, "cost": 0.03}'
```

### View Dashboard

```bash
curl http://localhost:8000/dashboard
```

## Architecture

- **FastAPI** - Modern Python web framework
- **SQLite** - Lightweight database for cost storage
- **In-Memory Circuit Breaker** - Fast failure detection

**Questions?** Open an issue or reach out!

## Sponsorship

This project follows the App Factory sponsorship model:

### $5/month - Supporter
- Sponsor badge on your GitHub profile
- Monthly sponsor update

### $25/month - Builder Circle
- Everything in Supporter
- Name listed in project Sponsors section (monthly refresh)
- Access to private sponsor Discord channel

### $100/month - Priority Maintainer
- Everything in Builder Circle
- Priority bug triage for your reports (max 2 issues/month)
- Response target: within 5 business days

### $1,000/month - Operator Advisory
- Everything in Priority Maintainer
- Dedicated async advisory support
- Service boundary: guidance and review only (no custom development included)

### $5,000 one-time - Custom Project Engagement
- Custom contract engagement
- Discovery required before kickoff
- Scope, timeline, and deliverables agreed in writing

Sponsor: https://github.com/sponsors/sdotwinter

