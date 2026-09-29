# prokname Docker image — multi-stage build for a small, reproducible CLI container.
# Usage:
#   docker build -t prokname .
#   docker run --rm prokname gen --stem Boyd --type person --rank species --genus Shigella --person-gender male
#   docker run --rm prokname route --source MAG --icnp-occupied no

FROM python:3.13-slim AS builder

WORKDIR /build

# Install build dependencies
RUN pip install --no-cache-dir hatchling

# Copy project files
COPY pyproject.toml README.md LICENSE DATA_LICENSE ./
COPY src/ ./src/
COPY scripts/ ./scripts/

# Build wheel
RUN pip install --no-cache-dir build && python -m build --wheel --no-isolation

# ---- Runtime stage ----
FROM python:3.13-slim AS runtime

LABEL org.opencontainers.image.title="prokname"
LABEL org.opencontainers.image.description="Prokaryotic nomenclature assistant (ICNP / SeqCode)"
LABEL org.opencontainers.image.license="MIT"
LABEL org.opencontainers.image.source="https://github.com/ZengZichao/ProkName"

WORKDIR /app

# Copy wheel from builder and install
COPY --from=builder /build/dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm -f /tmp/*.whl

# Non-root user for security
RUN useradd --create-home --shell /bin/bash prokname
USER prokname
WORKDIR /home/prokname

ENTRYPOINT ["prokname"]
CMD ["--help"]
