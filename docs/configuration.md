# Konfiguration & Account-Adressierung — Design-Spezifikation

Status: **Entwurf** · Branch: `config-consolidation` (Ziel: `main`) · Stand: 23. Juni 2026 · de-DE

> **Neutrales Fundament — keine DSGVO-Semantik.** Bewusst so erweiterbar, dass der
> `dsgvo`-Branch zusätzliche Keys/Sections und das vCard-Auto-Scan andockt, ohne
> das Format zu forken.

---

## 1. Ziel

Die heute LLM-schreibbare `accounts.jmd`-Registry wird ersetzt durch **ein
out-of-reach Config-Verzeichnis**. Konten werden per **`label`** adressiert; der
Server löst `label → Endpunkte/username/auth` intern auf. Dadurch verlassen
`username` (personenbezogenes Datum) und Server-Endpunkte den Server **nie** mehr
Richtung LLM.

## 2. Das Config-Verzeichnis

- **Default**: `~/.jmd-mcp-mail/` (plattformneutral: `Path.home() / ".jmd-mcp-mail"`).
- **Env-Override** für das *Verzeichnis*: `JMD_MCP_MAIL_HOME` (ein Pfad — keine PII).
- **Inhalt per Konvention** (kein Listing nötig):
  - `config.jmd` — Accounts (+ später DSGVO-Keys).
  - `*.vcf` / `*.pst` (top-level) — **reserviert für den `dsgvo`-Teil**
    (Kontakt-Auto-Import; `.pst` nur mit optionalem `libpff-python`); `main` legt
    das Verzeichnis nur an.
  - `contacts.md` (nur `dsgvo`) — Re-ID-Transkript der **laufenden** Session,
    serverseitig geschrieben und an den Session-Grenzen gelöscht; **privat halten**.
- **Sensibel, nicht geheim**: enthält `username` (personenbezogen; später
  Scoping-Adressen). Restriktive Verzeichnis-/Dateirechte empfohlen. **Keine
  Geheimnisse** — Passwörter/Tokens/Keys bleiben im **OS-Keyring**.

## 3. `config.jmd` — Schema (`main`-Teil)

```
# Account[]
- label: ionos
  imap: imap.ionos.de:993
  smtp: smtp.ionos.de:587
  username: andreas@ostermeyer.de
  auth: basic                       # basic | oauth2
  broker-client: outlook            # nur bei auth: oauth2
  from-name: Andreas Ostermeyer     # optional
```

- `label` ist Primärschlüssel (eindeutig).
- Der Parser **ignoriert unbekannte Keys** — der Erweiterungspunkt, an dem `dsgvo`
  `pseudonymize`, `scope-exclude`, `self-aliases` … ergänzt.

## 4. Label-API (Breaking Change)

- `read` / `write` / `delete (account, document)` → **IMAP**-Endpunkt des Accounts.
- `send (account, document)` → **SMTP**-Endpunkt.
- Der Server resolved `label → (host/port/tls, username, auth, broker-client,
  from-name)`. Die Credential-Auflösung (Keystore nach `(service, username)`)
  bleibt — nur dass `(service, username)` jetzt **aus dem Account** kommt, nicht
  vom LLM.
- Unbekanntes Label → `# Error / unknown_account`.

## 5. `accounts`-Tool: read-only Projektion

- `# Account[]` → nur `{ label, auth, broker-client? }`. **Kein** `username`,
  **keine** Endpunkte.
- `# PublicKey` → bleibt (für das Sealing der OAuth2-Tokens).
- `#! Account` → Schema (read-only).
- **Kein Upsert/Delete mehr.** Konten anlegen/ändern = `config.jmd` editieren
  (out-of-band).

## 6. `from-name`

`main` hat den per-`send`-Override bereits. Neu: ein **optionaler Account-Default**
in `config.jmd`. Der Server setzt den Anzeigenamen aus dem Account; ein per-Call
`from-name` überschreibt ihn weiterhin. Dein Name muss damit nicht je Sendung im
Kontext stehen.

## 7. Onboarding (geändert)

Statt „speichere mein Konto" (LLM schreibt) zeigt das LLM: **(a)** den
`config.jmd`-Account-Block zum Einfügen und **(b)** die Keystore-Seed-Befehle. Du
wendest beides out-of-band an. Konsistent mit der Keystore-Philosophie.

## 8. Migration

Best-effort: Existiert `~/.jmd-mcp-mail/config.jmd` nicht, aber eine alte
`accounts.jmd` (alter Pfad / `JMD_MCP_MAIL_ACCOUNTS_PATH`), werden deren Accounts
einmalig übernommen + eine Notiz geloggt. Sonst manuell.

## 9. Betroffene Dateien

| Datei | Rolle |
|---|---|
| `src/mail_mcp/_config.py` *(neu)* | Verzeichnis-Discovery (Default + `JMD_MCP_MAIL_HOME`), `config.jmd` laden/parsen, Account-Resolver `label → Account` |
| `src/mail_mcp/_endpoint.py` | `ConnectionInfo` aus einem Account-Record (statt `(service, username)`) |
| `src/mail_mcp/server.py` | vier Tools auf `account`-Parameter; `accounts`-Tool read-only |
| `src/mail_mcp/accounts.py` | von Registry (read/write) → read-only Projektion + Label-Resolver; Upsert/Delete raus |
| (Migrationshelfer) | einmalige Übernahme alter `accounts.jmd` |

## 10. Andockpunkt für `dsgvo` (nur Hinweis — nicht Teil von `main`)

Auf diesem Fundament ergänzt der `dsgvo`-Teil später: `config.jmd`-Keys
(`pseudonymize`, `pseudonymize-domain`, `mask-content-default`, per-Account
`scope-exclude` + strikter Whitelist-Modus, `self-aliases`), das **`*.vcf`-Auto-Scan**
im Config-Verzeichnis sowie den per-Datei-Import-Report (Dateiname →
imported/skipped/error).

---

*Offene Punkte, mit denen ich vorerst sinnvoll vorbelegt habe (bitte widersprechen,
falls anders gewünscht): `from-name` als optionaler Account-Default mit per-Call-
Override (§6); Migration best-effort-automatisch (§8). Umsetzung nach
ausdrücklichem Go.*
