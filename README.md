# Tapestry Client

Reference async client library for the Tapestry platform contracts.

## Usage

```python
from tapestry_client import TapestryClient

client = TapestryClient(
    base_url="https://tapestry.example.com",
    service_token="tap_service_...",
)
```

## Development

```bash
uv sync --extra dev
uv run pytest
```
