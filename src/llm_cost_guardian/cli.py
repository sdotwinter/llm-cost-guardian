"""CLI for LLM Cost Guardian."""

import asyncio
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from llm_cost_guardian import circuit_breaker, config, database, guardian

console = Console()


@click.group()
@click.version_option(version="0.1.0")
def main():
    """LLM Cost Guardian - Real-time monitoring and circuit-breaker for LLM API costs."""
    pass


@main.command()
def init():
    """Initialize LLM Cost Guardian configuration."""
    config_path = config.Config.default_config_path()
    
    if config_path.exists():
        if not click.confirm(f"Config already exists at {config_path}. Overwrite?"):
            console.print("[yellow]Cancelled.[/yellow]")
            return
    
    default_config = config.Config.create_default()
    default_config.save(config_path)
    console.print(f"[green]✓[/green] Created default config at {config_path}")


@main.command()
@click.option("--port", default=8000, help="Port to run proxy server on")
@click.option("--host", default="0.0.0.0", help="Host to bind to")
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path")
def proxy(port: int, host: str, config: str | None):
    """Start the LLM proxy server."""
    console.print(f"[green]Starting proxy server on {host}:{port}...[/green]")
    console.print("[yellow]Proxy server not yet implemented - use guardian.call() in your code[/yellow]")


@main.command()
@click.option("--port", default=8001, help="Port to run dashboard server on")
@click.option("--host", default="0.0.0.0", help="Host to bind to")
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path")
def dashboard(port: int, host: str, config: str | None):
    """View live cost dashboard."""
    console.print(f"[green]Starting dashboard server on {host}:{port}...[/green]")
    
    import uvicorn
    from llm_cost_guardian.server import app
    
    uvicorn.run(app, host=host, port=port)


@main.command()
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path")
def status(config: str | None):
    """Show current spending status."""
    config_path = config or config.Config.default_config_path()
    
    if not Path(config_path).exists():
        console.print(f"[red]No config found at {config_path}. Run 'llm-cost-guardian init' first.[/red]")
        return
    
    cfg = config.Config.from_file(config_path)
    db = database.CostDatabase(cfg.database_path)
    
    async def show_status():
        await db.init()
        
        global_spend = await db.get_global_daily_spend()
        top_users = await db.get_top_users(5)
        top_models = await db.get_top_models(5)
        
        await db.close()
        
        # Print summary
        console.print("\n[bold]Global Daily Spend[/bold]")
        console.print(f"  Total: ${global_spend:.2f} / ${cfg.limits.global_daily:.2f} ({global_spend/cfg.limits.global_daily*100:.1f}%)")
        
        # Top users table
        if top_users:
            console.print("\n[bold]Top Users[/bold]")
            table = Table(show_header=True)
            table.add_column("User ID")
            table.add_column("Total Cost", justify="right")
            table.add_column("Requests", justify="right")
            
            for user in top_users:
                table.add_row(
                    user["user_id"],
                    f"${user['total_cost']:.2f}",
                    str(user["request_count"]),
                )
            console.print(table)
        
        # Top models table
        if top_models:
            console.print("\n[bold]Top Models[/bold]")
            table = Table(show_header=True)
            table.add_column("Model")
            table.add_column("Total Cost", justify="right")
            table.add_column("Requests", justify="right")
            
            for model in top_models:
                table.add_row(
                    model["model"],
                    f"${model['total_cost']:.2f}",
                    str(model["request_count"]),
                )
            console.print(table)
    
    asyncio.run(show_status())


@main.command()
@click.argument("user_id")
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path")
def user_status(user_id: str, config: str | None):
    """Show status for a specific user."""
    config_path = config or config.Config.default_config_path()
    
    if not Path(config_path).exists():
        console.print(f"[red]No config found at {config_path}. Run 'llm-cost-guardian init' first.[/red]")
        return
    
    cfg = config.Config.from_file(config_path)
    db = database.CostDatabase(cfg.database_path)
    
    async def show_user_status():
        await db.init()
        
        user_spend = await db.get_user_daily_spend(user_id)
        user_requests = await db.get_user_request_count(user_id)
        
        await db.close()
        
        percent = (user_spend / cfg.limits.per_user_daily * 100) if cfg.limits.per_user_daily > 0 else 0
        
        console.print(f"\n[bold]User: {user_id}[/bold]")
        console.print(f"  Daily Spend: ${user_spend:.2f} / ${cfg.limits.per_user_daily:.2f} ({percent:.1f}%)")
        console.print(f"  Requests/min: {user_requests} / {cfg.limits.requests_per_minute}")
        
        if percent >= 90:
            console.print("  [red]⚠️  Near limit![/red]")
        elif percent >= 75:
            console.print("  [yellow]⚠️  At 75%+[/yellow]")
    
    asyncio.run(show_user_status())


@main.command()
@click.option("--config", "-c", type=click.Path(exists=True), help="Config file path")
@click.option("--days", default=7, help="Number of days to report")
def report(config: str | None, days: int):
    """Generate usage report."""
    config_path = config or config.Config.default_config_path()
    
    if not Path(config_path).exists():
        console.print(f"[red]No config found at {config_path}. Run 'llm-cost-guardian init' first.[/red]")
        return
    
    cfg = config.Config.from_file(config_path)
    db = database.CostDatabase(cfg.database_path)
    
    async def show_report():
        await db.init()
        
        history = await db.get_spending_history(days)
        top_users = await db.get_top_users(10)
        top_models = await db.get_top_models(10)
        top_prompts = await db.get_top_prompts(10)
        
        await db.close()
        
        console.print(f"\n[bold]Spending History (Last {days} days)[/bold]")
        if history:
            table = Table(show_header=True)
            table.add_column("Date")
            table.add_column("Total Cost", justify="right")
            
            for day in history:
                table.add_row(day["date"], f"${day['total_cost']:.2f}")
            console.print(table)
        else:
            console.print("  No data yet.")
        
        console.print(f"\n[bold]Top {len(top_users)} Users[/bold]")
        if top_users:
            table = Table(show_header=True)
            table.add_column("User ID")
            table.add_column("Total Cost", justify="right")
            table.add_column("Requests", justify="right")
            
            for user in top_users:
                table.add_row(user["user_id"], f"${user['total_cost']:.2f}", str(user["request_count"]))
            console.print(table)
        
        console.print(f"\n[bold]Top {len(top_models)} Models[/bold]")
        if top_models:
            table = Table(show_header=True)
            table.add_column("Model")
            table.add_column("Total Cost", justify="right")
            table.add_column("Requests", justify="right")
            
            for model in top_models:
                table.add_row(model["model"], f"${model['total_cost']:.2f}", str(model["request_count"]))
            console.print(table)
    
    asyncio.run(show_report())


@main.group()
def alert():
    """Manage budget alerts."""
    pass


@alert.command("add")
@click.option("--threshold", "-t", type=int, required=True, help="Threshold percentage (50, 75, 90)")
@click.option("--webhook-url", "-w", type=str, help="Webhook URL for notifications")
@click.option("--config", "-c", type=click.Path(), help="Config file path")
def alert_add(threshold: int, webhook_url: str, config: str | None):
    """Add a budget alert."""
    config_path = config or config.Config.default_config_path()
    
    cfg = config.Config.from_file(config_path)
    
    # Check if alert already exists
    for existing in cfg.alerts:
        if existing.threshold == threshold:
            existing.webhook_url = webhook_url or existing.webhook_url
            console.print(f"[yellow]Updated alert at {threshold}%[/yellow]")
            cfg.save(config_path)
            return
    
    cfg.alerts.append(config.AlertConfig(threshold=threshold, webhook_url=webhook_url or ""))
    cfg.save(config_path)
    console.print(f"[green]✓[/green] Added alert at {threshold}%")


@alert.command("list")
@click.option("--config", "-c", type=click.Path(), help="Config file path")
def alert_list(config: str | None):
    """List all budget alerts."""
    config_path = config or config.Config.default_config_path()
    
    if not Path(config_path).exists():
        console.print("[red]No config found. Run 'llm-cost-guardian init' first.[/red]")
        return
    
    cfg = config.Config.from_file(config_path)
    
    if not cfg.alerts:
        console.print("No alerts configured.")
        return
    
    console.print("\n[bold]Configured Alerts[/bold]")
    table = Table(show_header=True)
    table.add_column("Threshold")
    table.add_column("Webhook URL")
    
    for alert in sorted(cfg.alerts, key=lambda a: a.threshold):
        table.add_row(f"{alert.threshold}%", alert.webhook_url or "(none)")
    
    console.print(table)


if __name__ == "__main__":
    main()
