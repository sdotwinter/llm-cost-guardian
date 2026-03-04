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

## Sponsorware

This project is published as **Sponsorware**.

### What is Sponsorware?

Sponsorware is a licensing model where features and premium capabilities are exclusive to sponsors, while the base project remains open source.

### Access Tiers

| Tier | Price | Features |
|------|-------|----------|
| Free | $0 | Basic API, 3 tenants, SQLite storage |
| Supporter | $9/mo | Unlimited tenants, PostgreSQL support, API exports |
| Enterprise | $49/mo | Custom integrations, Priority support, SLA |

### How to Unlock

1. **Star** this repository
2. **Sponsor** via GitHub Sponsors at [github.com/sponsors/sdotwinter](https://github.com/sponsors/sdotwinter)
3. You'll receive access to the private Sponsorware repository with full features

### Why Sponsorware?

- Sustainable funding for open source maintenance
- Rewards contributors and supporters
- Keeps core functionality free and accessible

---

**Questions?** Open an issue or reach out!

---
*Built with 💜 by the OSS Sponsorware Factory*
