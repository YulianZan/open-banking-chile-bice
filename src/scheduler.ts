/**
 * scheduler.ts
 *
 * Cron job que ejecuta el scraper de open-banking-chile
 * y persiste los resultados en SQLite.
 *
 * Coloca este archivo en src/scheduler.ts dentro del repo clonado.
 */

import cron from "node-cron";
import Database from "better-sqlite3";
import path from "path";
import fs from "fs";
import { getBank } from "./index";

// ── Configuración ────────────────────────────────────────────────────────────

const DB_PATH = process.env.DB_PATH ?? path.join(process.cwd(), "data", "bank_data.db");
const LOG_PATH = process.env.LOG_PATH ?? path.join(process.cwd(), "data", "cron.log");
const CRON_SCHEDULE = process.env.CRON_SCHEDULE ?? "0 7 * * *"; // 7 AM diario

// Bancos a ejecutar: agrega más si es necesario
const BANKS_CONFIG: BankJob[] = [
  {
    id: "bice",
    rut: process.env.BICE_RUT ?? "",
    password: process.env.BICE_PASS ?? "",
    enabled: !!(process.env.BICE_RUT && process.env.BICE_PASS),
  },
  // {
  //   id: "falabella",
  //   rut: process.env.FALABELLA_RUT ?? "",
  //   password: process.env.FALABELLA_PASS ?? "",
  //   enabled: !!(process.env.FALABELLA_RUT && process.env.FALABELLA_PASS),
  // },
];

interface BankJob {
  id: string;
  rut: string;
  password: string;
  enabled: boolean;
}

// ── Logger ───────────────────────────────────────────────────────────────────

function log(msg: string): void {
  const line = `[${new Date().toISOString()}] ${msg}`;
  console.log(line);
  try {
    fs.appendFileSync(LOG_PATH, line + "\n");
  } catch {
    // silencioso si no puede escribir el log
  }
}

// ── Base de datos SQLite ─────────────────────────────────────────────────────

function initDb(): Database.Database {
  // Asegurar que el directorio existe
  const dir = path.dirname(DB_PATH);
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }

  const db = new Database(DB_PATH);

  db.exec(`
    CREATE TABLE IF NOT EXISTS movements (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      bank        TEXT    NOT NULL,
      date        TEXT    NOT NULL,
      description TEXT    NOT NULL,
      amount      REAL    NOT NULL,
      balance     REAL,
      fetched_at  TEXT    NOT NULL,
      UNIQUE(bank, date, description, amount)  -- evita duplicados
    );

    CREATE TABLE IF NOT EXISTS snapshots (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      bank        TEXT    NOT NULL,
      balance     REAL,
      fetched_at  TEXT    NOT NULL
    );

    CREATE TABLE IF NOT EXISTS runs (
      id          INTEGER PRIMARY KEY AUTOINCREMENT,
      bank        TEXT    NOT NULL,
      success     INTEGER NOT NULL,
      movements   INTEGER DEFAULT 0,
      error       TEXT,
      started_at  TEXT    NOT NULL,
      finished_at TEXT    NOT NULL
    );
  `);

  return db;
}

// ── Scraper runner ───────────────────────────────────────────────────────────

async function runScraper(job: BankJob, db: Database.Database): Promise<void> {
  const startedAt = new Date().toISOString();
  log(`▶  Iniciando scraper: ${job.id}`);

  const bank = getBank(job.id);
  if (!bank) {
    log(`✗  Banco no encontrado: ${job.id}`);
    return;
  }

  let success = false;
  let movCount = 0;
  let errorMsg: string | undefined;

  try {
    const result = await bank.scrape({
      rut: job.rut,
      password: job.password,
    });

    if (!result.success) {
      throw new Error("Scraper devolvió success=false");
    }

    const fetchedAt = new Date().toISOString();

    // Guardar snapshot de saldo
    db.prepare(`
      INSERT INTO snapshots (bank, balance, fetched_at)
      VALUES (?, ?, ?)
    `).run(job.id, result.balance ?? null, fetchedAt);

    // Insertar movimientos (ignorar duplicados por UNIQUE constraint)
    const insertMov = db.prepare(`
      INSERT OR IGNORE INTO movements (bank, date, description, amount, balance, fetched_at)
      VALUES (?, ?, ?, ?, ?, ?)
    `);

    const insertMany = db.transaction((movements: typeof result.movements): number => {
      let count = 0;
      for (const m of movements) {
        const info = insertMov.run(
          job.id,
          m.date,
          m.description,
          m.amount,
          (m as any).balance ?? null,
          fetchedAt,
        );
        if (info.changes > 0) count++;
      }
      return count;
    });

    movCount = insertMany(result.movements) as number;
    success = true;

    log(`✓  ${job.id}: saldo=$${result.balance?.toLocaleString("es-CL") ?? "N/A"}, movimientos nuevos=${movCount}/${result.movements.length}`);
  } catch (err: any) {
    errorMsg = err?.message ?? String(err);
    log(`✗  ${job.id}: ERROR - ${errorMsg}`);
  }

  // Registrar la ejecución
  db.prepare(`
    INSERT INTO runs (bank, success, movements, error, started_at, finished_at)
    VALUES (?, ?, ?, ?, ?, ?)
  `).run(job.id, success ? 1 : 0, movCount, errorMsg ?? null, startedAt, new Date().toISOString());
}

// ── Main ─────────────────────────────────────────────────────────────────────

async function main(): Promise<void> {
  log("═══════════════════════════════════════");
  log("  open-banking-chile scheduler iniciado");
  log(`  Cron: ${CRON_SCHEDULE} (TZ: ${process.env.TZ ?? "UTC"})`);
  log(`  DB:   ${DB_PATH}`);
  log("═══════════════════════════════════════");

  const db = initDb();
  const enabledBanks = BANKS_CONFIG.filter((b) => b.enabled);

  if (enabledBanks.length === 0) {
    log("⚠  No hay bancos configurados. Revisa las variables de entorno.");
    process.exit(1);
  }

  log(`  Bancos activos: ${enabledBanks.map((b) => b.id).join(", ")}`);

  // Ejecutar al inicio (run on startup)
  log("  Ejecutando scraper al inicio...");
  for (const job of enabledBanks) {
    await runScraper(job, db);
  }

  // Programar ejecuciones futuras
  cron.schedule(CRON_SCHEDULE, async () => {
    log(`⏰ Cron disparado: ${new Date().toISOString()}`);
    for (const job of enabledBanks) {
      await runScraper(job, db);
    }
    log("  Ciclo completado.");
  });

  log(`  Próxima ejecución programada según: ${CRON_SCHEDULE}`);
}

main().catch((err) => {
  console.error("Error fatal en scheduler:", err);
  process.exit(1);
});
