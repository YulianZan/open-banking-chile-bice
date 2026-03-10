services:
  open-banking:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: open-banking-chile
    restart: unless-stopped
    security_opt:
      - seccomp:unconfined
    shm_size: "256mb"
    volumes:
      - bank_data:/data
    environment:
      - BICE_RUT=${BICE_RUT}
      - BICE_PASS=${BICE_PASS}
      - BICE_MONTHS=${BICE_MONTHS:-3}
      - CRON_SCHEDULE=${CRON_SCHEDULE:-0 7 * * *}
      - TZ=America/Santiago
      - DB_PATH=/data/bank_data.db
      - LOG_PATH=/data/cron.log
      - PUPPETEER_SKIP_CHROMIUM_DOWNLOAD=true
      - CHROME_PATH=/usr/bin/chromium

volumes:
  bank_data:
    driver: local
