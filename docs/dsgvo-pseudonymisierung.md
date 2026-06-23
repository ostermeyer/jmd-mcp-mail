# DSGVO-Pseudonymisierung in jmd-mcp-mail — Design-Spezifikation

Status: **Entwurf / Designkonsens** · Branch: `dsgvo` · Stand: 23. Juni 2026 · de-DE

> Diese Spec hält den mit dem Maintainer abgestimmten Designkonsens fest und dient
> als Brücke zur Implementierung. Sie beschreibt *Verhalten und Datenfluss*, noch
> keinen fertigen Code.

---

## 1. Zweck und Einordnung

Ziel: **personenbezogene E-Mail-Identitäten am Rand von `jmd-mcp-mail` minimieren,
bevor sie in den LLM-Kontext (und damit potenziell ins Drittland) gelangen.**

Wichtig für die Einordnung: Den rechtlichen Rahmen für die *Zulässigkeit* des
Transfers tragen AVV (Art. 28) + DPF/SCC (Kapitel V) + idealerweise EU-Residenz
(siehe [`claude-cowork-code-dsgvo-konformitaet.md`](claude-cowork-code-dsgvo-konformitaet.md)).
Die Pseudonymisierung ist **Datensparsamkeit und Defense-in-Depth** (Art. 5 Abs. 1
lit. c, Art. 25, Erwägungsgrund 28): Sie stärkt die Berechtigtes-Interesse-Abwägung
für die Daten Dritter und reduziert, was im Schadensfall (CLOUD-Act-Zugriff,
DPF-Wegfall) faktisch abfließt. Sie **muss die Rechtsgrundlage nicht allein tragen.**

**Abgrenzung:** Betrifft nur `jmd-mcp-mail`. `jmd-mcp-oauth2` transportiert keine
Mail-Inhalte und bleibt unberührt. Das Feature lebt ausschließlich auf dem Branch
`dsgvo`; `main` bleibt eigenständige OSS-Basis.

---

## 2. Schutzumfang (bewusst flach)

**Im Scope:**
- Adressen **und** Anzeigenamen in den Headern (`From`, `To`, `Cc`, `Reply-To`,
  `Sender`).
- E-Mail-Adressen, die per **Regex** in Betreff und Body auftauchen.

**Nicht im Scope:**
- Tiefe Body-NER (Namen, Telefonnummern, Anschriften im Fließtext). Begründung:
  niedrige Präzision, zerschießt genau den Text, den der Nutzer lesen/zusammenfassen
  will; schlechtes Kosten/Nutzen-Verhältnis. Der Rest-Body-Inhalt wird durch den
  rechtlichen Rahmen abgedeckt, nicht durch dieses Feature.

Das ist ein **bewusster Trade-off** (Usability ↔ Minimierung), bei Bedarf später
nachschärfbar.

---

## 3. Pseudonym-Form

Form im Transkript: **`Vorname <key>`**, z. B. `Alice <a1b2c3>`.

- **`<key>`** — deterministischer **Einweg-Token**: `HMAC-SHA256(secret, addr_norm)`,
  gekürzt auf ~6–8 Zeichen (Base32, klein­geschrieben). Nicht entschlüsselbar, nur
  **abgleichbar**. Über Sessions hinweg stabil (gleiche Adresse → gleicher Token),
  **ohne** dass eine Tabelle persistiert wird. Disambiguiert Gleichnamige
  (`Alice <a1b2c3>` vs. `Alice <d4e5f6>`).
- **`Vorname`** — aus dem Anzeigenamen extrahiert. Regel: **nur wenn eindeutig
  extrahierbar**, sonst reiner `<key>`. Konservativ bei unklaren Anzeigenamen:
  - `"Alice Schmidt"` → `Alice <key>`
  - `"Schmidt, Alice"`, Rollen-Adressen (`"Support Team"`), nicht-lateinische
    Schrift, **kein** Anzeigename → nur `<key>`
- Der Vorname ist selbst ein **Rest-PII-Fragment**, das weiterhin übermittelt wird —
  bewusst akzeptiert zugunsten der Lesbarkeit, ehrlich zu dokumentieren.

**Domain-Disambiguierung (opt-in, default aus):** optional ein Domain-Token, das
„gleiche Firma"-Beziehungen erhält (z. B. `Alice <key>@<dom>`).

---

## 4. Token-Verfahren & Secret

- **Secret:** 32 zufällige Bytes, im OS-Keystore (bestehendes Credential-Muster,
  Service `jmd-mcp-mail`). Beim ersten Bedarf einmalig erzeugt und gespeichert.
- Determinismus über das Secret → stabile Tokens ohne Mapping-Tabelle.
- Einwegigkeit → aus dem Token allein lässt sich die Adresse nicht rekonstruieren
  (man bräuchte zusätzlich die Mailbox **und** das Secret).
- **Normalisierung** der Adresse vor dem HMAC (lowercase, trim). Offen: ob
  Gmail-Punkte/`+tags` normalisiert werden (siehe §9).

---

## 5. Persistenz & Auflösung

- **Keine** Mapping-Tabelle at rest. Persistiert wird **nur** das Secret (Keystore).
- **Vorwärts** (echt → Pseudonym): on-the-fly beim Rendern.
- **Rückwärts** (Pseudonym → echt): **In-Memory-Map**, beim Lesen befüllt. Für einen
  Token, den der Server diese Session noch nicht gesehen hat, wird die Map durch
  erneutes Scannen des betroffenen Ordners rekonstruiert (Adressen hashen, Treffer
  suchen).
- Lebensdauer = Serverprozess; geht bei Neustart verloren und wird lazy neu
  aufgebaut. Konsequenz: **gestrige Transkript-Tokens sind nach Neustart nur via
  Mailbox-Scan auflösbar** — kein Reveal-Feature (siehe §10).

---

## 6. Datenfluss

### Ausgehend (Server → LLM) — beim `read`
Nach dem Parsen einer Nachricht/eines Folders in das Dict, **vor** dem `serialize`:
Header-Adressen + Anzeigenamen → `Vorname <key>` tauschen; Betreff/Body per Regex
nach Adressen durchsuchen und ersetzen; dabei die In-Memory-Rückwärts-Map befüllen.
- Eingriffspunkt: [`src/mail_mcp/imap/_parse.py`](../src/mail_mcp/imap/_parse.py)
  (`message_to_dict` / `folder_to_dict`) und
  [`src/mail_mcp/imap/read.py`](../src/mail_mcp/imap/read.py).

### Eingehend (LLM → Server)
- **Senden:** `to`/`cc`/`bcc`/`from-name` können Pseudonyme tragen. `<key>` → echte
  Adresse auflösen; **unbekannter Token → Fehler** (Containment). Eingriffspunkt:
  [`src/mail_mcp/smtp.py`](../src/mail_mcp/smtp.py).
- **Query-Kriterien:** `from`/`to`-Prädikate können Pseudonyme tragen → vor dem Bau
  der IMAP-SEARCH auflösen. Eingriffspunkt:
  [`src/mail_mcp/imap/_criteria.py`](../src/mail_mcp/imap/_criteria.py).
- **Write (Flags/Move):** arbeitet mit `id`/`folder`, keine Adressen → unberührt.

---

## 7. Per-Account-Schalter

- **Default AN** (Privacy by Default, Art. 25 Abs. 2), pro Account abschaltbar.
- Neues Feld in der Account-Registry, z. B. `pseudonymize: true|false` (Default
  `true`) sowie `pseudonymize_domain: true|false` (Default `false`, opt-in).
  Eingriffspunkt: [`src/mail_mcp/accounts.py`](../src/mail_mcp/accounts.py)
  (`Account`-Dataclass + Validierung + `as_jmd_dict`).

---

## 8. Containment-Eigenschaft

Der LLM kann nur Tokens adressieren, die der Server kennt → er kann **keine echte
Adresse erfinden**. Neue Empfänger bringt der **Nutzer** ein (echte Adresse als
Eingabe). Hinweis: Eine vom Nutzer im Chat eingegebene neue Adresse gelangt damit in
den Kontext — das ist bei einer ausdrücklich nutzerinitiierten Aktion akzeptiert.

---

## 9. Grenzfälle / offene Implementierungsfragen

- Normalisierung: Gmail-Punkte / `+tags` zusammenführen oder belassen?
- Token-Länge vs. Kollisionsrisiko (~6–8 Zeichen).
- Namens-Heuristik & Internationalisierung (nicht-lateinisch, „Nachname, Vorname").
- Body-Regex: Adressen in zitierten Replies, `mailto:`-Links; **obfuskierte**
  Formen („alice at acme dot com") bleiben außerhalb des Scopes.
- Multipart/HTML: Tausch in **beiden** Body-Alternativen (Text + HTML) konsistent.
- Kosten des Rückwärts-Map-Rebuilds bei großen Ordnern.
- **Sende-Bestätigung:** Die Erfolgsmeldung von `send` darf den aufgelösten
  **echten** Empfänger nicht zurück in den Kontext spiegeln — Bestätigung mit dem
  **Pseudonym** ausgeben (vgl. aktuelle Antwort in `smtp.py`, die `to` echo't).
- Anhänge: Dateinamen können Namen enthalten — vorerst außerhalb des Scopes.

---

## 10. Außerhalb des Scopes (entschieden)

- **Reveal-Feature:** bewusst **keines**. Braucht der Nutzer die echte Adresse hinter
  `Alice <key>`, schlägt er die Mail in seinem normalen Mail-Client nach — Klartext
  berührt nie den LLM-Pfad.
- **`jmd-mcp-oauth2`:** unverändert.
- **Tiefe Body-PII / NER:** siehe §2.

---

## 11. Betroffene Dateien (Implementierungsüberblick)

| Datei | Rolle |
|---|---|
| `src/mail_mcp/_pseudonym.py` *(neu)* | Token-Erzeugung, Secret-Bootstrap, In-Memory-Rückwärts-Map, Namens-Extraktion |
| `src/mail_mcp/imap/_parse.py` | Ausgehender Tausch (Header + Body-Regex) |
| `src/mail_mcp/imap/read.py` | Einbindung im Read-Pfad |
| `src/mail_mcp/smtp.py` | Eingehende Auflösung (Empfänger, `from-name`), Bestätigung |
| `src/mail_mcp/imap/_criteria.py` | Eingehende Auflösung (Suchprädikate) |
| `src/mail_mcp/accounts.py` | Per-Account-Schalter (Default an) |

---

## 12. Adressbuch / Kontakt-Import

Ermöglicht das Adressieren von Personen, deren Mail man (noch) nicht gelesen hat —
z. B. weil das Konto serverseitig für den MCP-Server gesperrt ist —, ohne dass deren
echte Adresse je in den LLM-Kontext gelangt. Konzeptionell ein **persistent
gepflegter Seed für dieselbe In-Memory-Rückwärts-Map** wie die Lese-Pseudonyme:
gleicher HMAC-Token ⇒ konsistente Identität, egal ob aus gelesener Mail oder Import.

### 12.1 Quellen (nur Pfade, nie Inhalte über die Tool-Grenze)

- **Primär:** wiederholbares CLI-Argument am Entrypoint (`--contacts <pfad>`),
  gesetzt in der MCP-Server-Konfiguration (`args` im mcpServers-Block).
- **Alternativ:** eine Umgebungsvariable (`JMD_MCP_MAIL_CONTACT_SOURCES`,
  pfadgetrennte Liste) — unabhängig davon, wie/woher sie gesetzt wird (kein
  dotenv-Parsing serverseitig, schlicht `os.environ`).
- Ein Pfad ist kein PII; die Dateien liegen außerhalb des Servers
  (Nutzer-Verantwortung) und werden serverseitig gelesen.

### 12.2 Formate

- **vCard** (`.vcf`) via `vobject` — primär (Apple/Google/Outlook/Thunderbird).
- **CSV** via stdlib `csv` mit tolerantem Spalten-Mapping (Outlook-/Google-Header).

### 12.3 Speicherung & Lebenszyklus

- **Strikt in-memory**, kein `contacts.jmd`, nichts at rest. Die einzigen
  PII-Dateien sind die nutzereigenen Exporte.
- Startup parst alle Quellen → In-Memory. `reimport` lädt sie zur Laufzeit neu.

### 12.4 Label-Ableitung — Form `<Namensteil> <key>`

`<key>` (Einweg-HMAC, identisch zur Lese-Pseudonymisierung) ist **immer** Bestandteil
und trägt die Eindeutigkeit. Der Namensteil ist reine Lesbarkeits-/Wiedererkennungs-
Hilfe:

- Basis: **Vorname** (aus vCard `N`, wo Vor-/Nachname klar getrennt sind).
- Bei Vornamen-Kollision: + **kürzestes eindeutiges Nachnamen-Präfix** + „."
  (`Rebecca Sch.` vs. `Rebecca Spe.`).
- Mehrere Adressen pro Kontakt: **je Adresse ein diskreter Eintrag/Token**, im Label
  per vCard-`TYPE` unterschieden (`(geschäftlich)` / `(privat)`).
- Kein Nachname / Trennung nicht möglich → nur `Vorname <key>` bzw. reiner `<key>`.

Beispiele: `Rebecca <k7f2a9>`, `Rebecca Sch. <k7f2a9>`,
`Rebecca Sch. (geschäftlich) <k7f2a9>`.

### 12.5 Tool-Oberfläche

- Eigenes **`contacts`**-Tool: `# Contacts { reimport }` → `# Contacts[]` mit
  `(label, token)`.
- **Rückgabe-Invariante:** nur `count` + `(label, token)`; **niemals** Adressen oder
  Roh-Inhalt — unabhängig vom Pfad. Damit ist auch ein vom LLM frei genannter oder
  halluzinierter Pfad harmlos (es kommt keine Adresse zurück).

### 12.6 Auflösung beim Senden / Suchen

- `send` löst `to`/`cc`/`bcc` per **Label *oder* Token** → echte Adresse
  (serverseitig, nie im Kontext). Unbekannt → `unknown_pseudonym` (Containment).
- Der **Lese-Pfad** reichert den Namensteil aus den Kontakten an
  (`Rebecca Sch. <key>` statt `Rebecca <key>`), wenn die Adresse bekannt ist —
  gleicher `<key>`, gleiche Identität.

### 12.7 Betroffene Dateien

| Datei | Rolle |
|---|---|
| `src/mail_mcp/_contacts.py` *(neu)* | Quellen-Discovery (args/env), vCard/CSV-Parser, Label-Ableitung, Seed der In-Memory-Map |
| `src/mail_mcp/_pseudonym.py` | gemeinsame Token-Erzeugung + Rückwärts-Map (von Kontakten mitgenutzt) |
| `src/mail_mcp/server.py` | `contacts`-Tool + Startup-Import; Label-Anreicherung im Read-Pfad |
| `src/mail_mcp/smtp.py` / `imap/_criteria.py` | Auflösung per Label (zusätzlich zum Token) |

---

*Kern-Pseudonymisierung umgesetzt (Commit f58215f). Adressbuch / Kontakt-Import:
Designkonsens 2026-06-23, Umsetzung nach ausdrücklichem Go.*
