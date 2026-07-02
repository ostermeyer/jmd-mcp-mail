# Anonymisierungsregeln für die Arbeit mit KI / LLM

**Zweck:** Diese Regeln legen fest, welche Daten **vor der Übertragung an ein Sprachmodell (LLM)** entfernt oder maskiert werden müssen. Ziel ist der Schutz personenbezogener und vertraulicher Informationen bei KI-gestützter Wissensarbeit (z. B. Auswertung von E-Mails, Dokumenten, Protokollen).

**Gilt für:** alle Teammitglieder, die KI-Tools mit Projekt- oder Kundendaten nutzen.

---

## Grundprinzip

Rohdateien (z. B. `.eml`, `.docx`, `.pdf`) bleiben unverändert auf der Platte. **Ins Modell geht nur eine bereinigte Fassung.** Praktisch heißt das: Dateien werden zuerst durch einen Maskierungsschritt geschickt, der sensible Muster ersetzt – erst das Ergebnis wird an die KI gegeben.

> Faustregel: Was nicht für die Aufgabe gebraucht wird, kommt erst gar nicht ins Modell. Im Zweifel maskieren.

---

## Was maskiert wird

| Datentyp | Beispiel | Ersetzen durch |
|----------|----------|----------------|
| E-Mail-Adresse | `vorname.nachname@firma.xy` | `[email]` |
| Telefon-/Mobilnummer | `+49 89 459926-90`, `0151 65577200` | `[telefon]` |
| IP-Adresse (v4/v6) | `10.20.30.40`, `fe80::1` | `[ip]` |
| Realer Servername / Hostname / FQDN | `sapprd01.firma.intern`, `https://sap-erp.firma.com` | `[server]` |
| Port / Host:Port | `:50000`, `sapprd01:3200` | `[port]` |
| (optional) Echte Kundennamen | `Musterfirma GmbH` | `Kunde A` / Pseudonym |
| (optional) Postanschriften | `Mühldorfstr. 8, 81671 München` | `[adresse]` |

**Hinweis Technik:** Statt realer Systemkennungen abstrakt benennen – z. B. „PRD-System", „QAS-System", „Schnittstelle X" statt `sapprd01.firma.intern:3200`. So bleibt die fachliche Aussage erhalten, ohne Infrastruktur preiszugeben.

**Immer ins Modell, statt Klardaten:** Namen → Rolle/Pseudonym (z. B. „Ansprechpartner Kunde A (technisch)"), Adresse → Stadt/Region falls nötig.

---

## Weitere DSGVO-relevante Daten (manuell prüfen)

Diese Kategorien sind personenbezogene Daten i. S. d. DSGVO und sollten vor der KI-Nutzung entfernt oder pseudonymisiert werden. Viele lassen sich nicht zuverlässig per Regex erfassen – hier ist manuelle Prüfung nötig.

**Identifikatoren & Kontakt**

- Vollständige Klarnamen → Rolle/Pseudonym („Kunde A (technisch)")
- Postanschriften, Standort-/GPS-Daten
- Personalnummern, Mitarbeiter-/Kundennummern, Vertrags-/Aktenzeichen
- Benutzernamen / Logins / User-IDs, Online-Kennungen, Geräte-IDs, MAC-Adressen
- Kfz-Kennzeichen

**Ausweis- & Behördennummern**

- Personalausweis-/Reisepassnummer
- Steuer-ID / USt-IdNr., Sozialversicherungsnummer

**Finanzdaten**

- IBAN, BIC, Kontonummern, Kreditkartennummern

**Zugang & Links (oft übersehen)**

- Meeting-Links, Meeting-IDs und Passcodes (z. B. Teams/Zoom)
- Persönliche/freigegebene Dokument-Links (z. B. personalisierte SharePoint-/OneDrive-URLs)
- Session-IDs, Tokens (siehe auch nächster Abschnitt)

**Besondere Kategorien (Art. 9 DSGVO – höchste Sensibilität, möglichst gar nicht eingeben)**

- Gesundheitsdaten (auch beiläufig: Krankheit, Reha, Schwangerschaft, „in Elternzeit" ist grenzwertig)
- Religion/Weltanschauung, ethnische Herkunft, politische Meinung
- Gewerkschaftszugehörigkeit, Sexualleben/-orientierung
- Biometrische Daten, genetische Daten
- Fotos/Bilder von Personen, Unterschriften

**HR-/Bewertungsdaten**

- Gehalt, Bonus, Leistungsbeurteilungen, Bewerberdaten

> Bei besonderen Kategorien (Art. 9) gilt: im Zweifel nicht eingeben, auch nicht maskiert – das Risiko der Re-Identifikation aus dem Kontext ist zu hoch.

---

## Was NIE ins Modell gehört (unabhängig vom Format)

- Zugangsdaten, Passwörter, API-Keys, Tokens
- Vertrags-/Stundensätze und sonstige Vertragssummen
- NDA-Inhalte (nur als Sachverhalt beschreiben, keine wörtlichen Detailinhalte)
- Bei Unsicherheit: nicht eingeben → Geschäftsleitung fragen

---

## Maskierungsmuster (Regex)

Diese Muster decken die häufigsten Fälle ab:

```text
E-Mail:     [A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}                    → [email]
Telefon:    (?:\+?\d[\d\s().\-/]{6,}\d)                                        → [telefon]
IPv4:       \b(?:\d{1,3}\.){3}\d{1,3}\b                                        → [ip]
IPv6:       \b(?:[A-Fa-f0-9]{1,4}:){2,7}[A-Fa-f0-9]{1,4}\b                     → [ip]
Server/FQDN:\b(?:[a-zA-Z0-9-]+\.)+(?:intern|local|corp|com|net|de|io)\b        → [server]
Host:Port:  \b[a-zA-Z0-9.-]+:\d{2,5}\b                                         → [server]:[port]
Port:       (?<=:)\d{2,5}\b                                                    → [port]
```

**Reihenfolge beachten:** Erst E-Mail maskieren, dann FQDN/Server, dann IP, dann Telefon, dann Port – sonst frisst das Telefonmuster Teile von IPs. Im Skript ist die Reihenfolge der Liste entscheidend.

**Hinweise:**
- *Telefon:* greift ab ca. 7 Stellen inkl. typischer Trenner. Kurze Zahlenfolgen (z. B. Jahreszahlen) werden bewusst nicht erfasst.
- *Server/FQDN:* das Muster erfasst nur gängige TLDs/internen Suffixe – seltene Endungen ggf. ergänzen.
- *IP/Port:* nach der Maskierung stichprobenartig prüfen, da z. B. Versionsnummern (`1.2.3.4`) wie IPs aussehen können.

---

## Empfohlener Ablauf (Sandbox-Skript)

Beispiel: alle E-Mails eines Ordners bereinigen und nur den maskierten Text weiterverarbeiten.

```python
import re, glob, email
from email import policy

# Reihenfolge ist wichtig: E-Mail -> Server/FQDN -> IP -> Telefon -> Host:Port -> Port
MASKEN = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[email]"),
    (re.compile(r"\b(?:[a-zA-Z0-9-]+\.)+(?:intern|local|corp|com|net|de|io)\b"), "[server]"),
    (re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[ip]"),
    (re.compile(r"\b(?:[A-Fa-f0-9]{1,4}:){2,7}[A-Fa-f0-9]{1,4}\b"), "[ip]"),
    (re.compile(r"\+?\d[\d\s().\-/]{6,}\d"), "[telefon]"),
    (re.compile(r"\b[a-zA-Z0-9.\-\[\]]+:\d{2,5}\b"), "[server]:[port]"),
]

def maskiere(text: str) -> str:
    for muster, ersatz in MASKEN:
        text = muster.sub(ersatz, text)
    return text

def eml_text(pfad: str) -> str:
    with open(pfad, "rb") as f:
        msg = email.message_from_binary_file(f, policy=policy.default)
    teile = []
    if msg.is_multipart():
        for p in msg.walk():
            if p.get_content_type() == "text/plain":
                teile.append(p.get_content())
    else:
        teile.append(msg.get_content())
    return "\n".join(teile)

for datei in glob.glob("*.eml"):
    sauber = maskiere(eml_text(datei))
    # -> nur 'sauber' an die KI / in Ausgaben geben
    print(sauber)
```

---

## Grenzen / Wichtig

- **Nachträglich gilt nicht:** Was bereits in einer laufenden KI-Sitzung gelesen wurde, lässt sich nicht zurückholen. Deshalb **vor** dem Einlesen maskieren.
- Regex ist nicht perfekt. Ungewöhnliche Formate (z. B. Telefonnummern als Fließtext) können durchrutschen – maskiertes Ergebnis stichprobenartig prüfen.
- Diese Regeln ersetzen keine rechtliche Bewertung. Bei NDA-/datenschutzkritischen Fällen im Zweifel die Geschäftsleitung einbeziehen.

---

*Stand: 2026-06-23 · Pflege: Andreas Töpperwien · Rückfragen an die Geschäftsleitung.*
