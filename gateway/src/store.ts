import Database from "better-sqlite3";
import { createHash, randomUUID } from "node:crypto";
import { dirname } from "node:path";
import { mkdirSync } from "node:fs";

export type Installation = {
  id: string;
  label: string;
  createdAt: string;
  lastUsedAt: string | null;
  revokedAt: string | null;
  uses: number;
};

type InviteRow = {
  id: string;
  label: string;
  expires_at: string | null;
  used_at: string | null;
  revoked_at: string | null;
};

type InstallationRow = {
  id: string;
  label: string;
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
  uses: number;
};

export class GatewayStore {
  private readonly db: Database.Database;

  constructor(path: string) {
    if (path !== ":memory:") mkdirSync(dirname(path), { recursive: true });
    this.db = new Database(path);
    this.db.pragma("journal_mode = WAL");
    this.db.pragma("foreign_keys = ON");
    this.db.pragma("busy_timeout = 5000");
    this.migrate();
  }

  seedInvite(code: string, label = "development") {
    const now = new Date().toISOString();
    this.db.prepare(`
      INSERT OR IGNORE INTO invites (id, code_hash, label, created_at)
      VALUES (?, ?, ?, ?)
    `).run(randomUUID(), hashSecret(code), label, now);
  }

  createInvite(code: string, label: string, expiresAt: string | null) {
    const now = new Date().toISOString();
    this.db.prepare(`
      INSERT INTO invites (id, code_hash, label, created_at, expires_at)
      VALUES (?, ?, ?, ?, ?)
    `).run(randomUUID(), hashSecret(code), label, now, expiresAt);
  }

  consumeInvite(inviteCode: string, installationId: string, tokenHash: string): Installation | null {
    const now = new Date().toISOString();
    const inviteHash = hashSecret(inviteCode);
    this.db.exec("BEGIN IMMEDIATE");
    try {
      const invite = this.db.prepare(`
        SELECT id, label, expires_at, used_at, revoked_at
        FROM invites WHERE code_hash = ?
      `).get(inviteHash) as InviteRow | undefined;
      if (!invite || invite.used_at || invite.revoked_at || (invite.expires_at && invite.expires_at <= now)) {
        this.db.exec("ROLLBACK");
        return null;
      }
      this.db.prepare(`
        INSERT INTO installations (id, token_hash, label, created_at, uses)
        VALUES (?, ?, ?, ?, 0)
      `).run(installationId, tokenHash, invite.label, now);
      const consumed = this.db.prepare(`
        UPDATE invites
        SET used_at = ?, installation_id = ?
        WHERE id = ? AND used_at IS NULL AND revoked_at IS NULL
      `).run(now, installationId, invite.id);
      if (consumed.changes !== 1) {
        this.db.exec("ROLLBACK");
        return null;
      }
      this.db.exec("COMMIT");
      return { id: installationId, label: invite.label, createdAt: now, lastUsedAt: null, revokedAt: null, uses: 0 };
    } catch (error) {
      this.safeRollback();
      throw error;
    }
  }

  authenticate(tokenHash: string): Installation | null {
    const row = this.db.prepare(`
      SELECT id, label, created_at, last_used_at, revoked_at, uses
      FROM installations WHERE token_hash = ?
    `).get(tokenHash) as InstallationRow | undefined;
    if (!row || row.revoked_at) return null;
    const now = new Date().toISOString();
    this.db.prepare(`
      UPDATE installations SET last_used_at = ?, uses = uses + 1 WHERE id = ?
    `).run(now, row.id);
    return {
      id: row.id,
      label: row.label,
      createdAt: row.created_at,
      lastUsedAt: now,
      revokedAt: row.revoked_at,
      uses: row.uses + 1,
    };
  }

  listInstallations(): Installation[] {
    const rows = this.db.prepare(`
      SELECT id, label, created_at, last_used_at, revoked_at, uses
      FROM installations ORDER BY created_at DESC
    `).all() as InstallationRow[];
    return rows.map((row) => ({
      id: row.id,
      label: row.label,
      createdAt: row.created_at,
      lastUsedAt: row.last_used_at,
      revokedAt: row.revoked_at,
      uses: row.uses,
    }));
  }

  revokeInstallation(id: string) {
    return this.db.prepare(`
      UPDATE installations SET revoked_at = COALESCE(revoked_at, ?) WHERE id = ?
    `).run(new Date().toISOString(), id).changes === 1;
  }

  ready() {
    return this.db.prepare("SELECT 1 AS ok").get() !== undefined;
  }

  close() {
    if (this.db.open) this.db.close();
  }

  private migrate() {
    this.db.exec(`
      CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
      );
      CREATE TABLE IF NOT EXISTS installations (
        id TEXT PRIMARY KEY,
        token_hash TEXT NOT NULL UNIQUE,
        label TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_used_at TEXT,
        revoked_at TEXT,
        uses INTEGER NOT NULL DEFAULT 0
      ) STRICT;
      CREATE TABLE IF NOT EXISTS invites (
        id TEXT PRIMARY KEY,
        code_hash TEXT NOT NULL UNIQUE,
        label TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT,
        used_at TEXT,
        revoked_at TEXT,
        installation_id TEXT UNIQUE REFERENCES installations(id)
      ) STRICT;
      CREATE INDEX IF NOT EXISTS invites_active_idx ON invites (code_hash, used_at, revoked_at);
      INSERT OR IGNORE INTO schema_migrations (version, applied_at) VALUES (1, datetime('now'));
    `);
  }

  private safeRollback() {
    try {
      this.db.exec("ROLLBACK");
    } catch {
      // The transaction may already have been rolled back by SQLite.
    }
  }
}

export function hashSecret(value: string) {
  return createHash("sha256").update(value).digest("hex");
}
