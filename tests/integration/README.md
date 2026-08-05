# Integration tests

These need a running store, so they are skipped unless `TICK_LAB_TEST_S3=1`.

    # 1. bring up the stack (MinIO node included)
    docker compose up -d

    # 2. load the fixture ticks from your machine
    cd tick-lab && .venv/bin/tick-lab load tests/fixtures/

    # 3. run the parity test inside the image
    cd .. && docker compose run --rm \
      -e TICK_LAB_TEST_S3=1 \
      openbb-api python -m pytest /workspace/tests/integration -q
