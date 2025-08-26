import typer
import uvicorn
from shared.dsx_logging import dsx_logging

# Ensure connector is registered via decorators
import connectors.aws_s3.aws_s3_connector  # noqa: F401

app = typer.Typer(help="Start the AWS S3 Connector.")

@app.command()
def start(
    host: str = typer.Option("0.0.0.0", help="Host to bind the FastAPI server."),
    port: int = typer.Option(8591, help="Port to bind the FastAPI server."),
    reload: bool = typer.Option(False, help="Enable autoreload (development only)."),
    workers: int = typer.Option(1, help="Number of Uvicorn worker processes.")
):
    """
    Launch the AWS S3 Connector FastAPI app.
    """
    dsx_logging.info(
        f"Starting AWS S3 Connector on {host}:{port} "
        f"(reload={'on' if reload else 'off'}, workers={workers})"
    )
    uvicorn.run(
        "connectors.framework.dsx_connector:connector_api",
        host=host,
        port=port,
        reload=reload,
        workers=workers
    )


if __name__ == "__main__":
    app()
