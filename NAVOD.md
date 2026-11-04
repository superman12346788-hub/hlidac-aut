# Hlídač aut – návod k zprovoznění

Bot každých 30 minut projde Bazoš a Sauto. U nových inzerátů spočítá medián cen
podobných aut a pošle ti do Telegramu zprávu, když je auto výrazně pod trhem nebo
inzerát obsahuje slova jako „spěchá“, „dědictví“, „kontrolka“.

Zpráva obsahuje: cenu, odhad tržní ceny, slevu v %, odhad prodejní ceny, odhad marže
po tvých nákladech, rok, km, lokalitu, fotku a odkaz.

> Nastavení je jednodušší z počítače než z mobilu (zabere to asi 15 minut).

---

## Krok 1 – Telegram bot (5 min)

1. V Telegramu najdi **@BotFather** → napiš `/newbot` → zadej jméno (např. `Hlidac aut`)
   a uživatelské jméno končící na `bot` (např. `filip_auta_bot`).
2. BotFather ti pošle **token** (vypadá jako `7123456789:AAH...`). Ulož si ho.
3. Najdi svého nového bota a napiš mu cokoliv (třeba `ahoj`). **Bez toho ti nemůže psát.**
4. Najdi **@userinfobot**, napiš mu `/start` → pošle ti tvoje **Id** (číslo). To je `chat_id`.

## Krok 2 – GitHub (10 min)

1. Založ si účet na **github.com** (zdarma).
2. Vpravo nahoře **+ → New repository** → název `hlidac-aut` → zvol **Private** →
   **Create repository**.
3. Klikni na **uploading an existing file** a přetáhni sem **všechny soubory ze složky**
   (včetně složky `.github`). Pak **Commit changes**.
   - Pokud se složka `.github` nenahraje (je skrytá), dej **Add file → Create new file**,
     jako název napiš `.github/workflows/bot.yml` a vlož do něj obsah souboru `bot.yml`.
4. **Settings → Secrets and variables → Actions → New repository secret** a přidej:
   - `TELEGRAM_TOKEN` = token od BotFathera
   - `TELEGRAM_CHAT_ID` = tvoje Id
   - `ANTHROPIC_API_KEY` = jen pokud chceš zhodnocení od Claude (viz níže)
5. **Settings → Actions → General → Workflow permissions** → zvol
   **Read and write permissions** → Save.
6. Záložka **Actions** → vlevo **Hlídač aut** → **Run workflow**.

Při prvním spuštění ti přijde zpráva „Začínám hlídat…“. Bot si zapamatuje všechny
současné inzeráty a od dalšího běhu posílá jen nové.

**Limity zdarma:** soukromý repozitář má 2000 minut měsíčně. Běh každých 30 minut
spotřebuje zhruba 1500, takže se do limitu vejdeš. Interval nezkracuj pod 30 minut.

## Krok 3 – Nastav si, co hlídat

Na GitHubu otevři `config.yaml` → ikona tužky → uprav → **Commit changes**.

- **searches** – modely, ceny, rok, km. Hodnotu `sauto_model` zjistíš z adresy na Sauto:
  `sauto.cz/inzerce/osobni/volkswagen/passat` → `volkswagen:passat`.
- **min_discount_pct** – o kolik % musí být auto levnější než trh, aby přišla zpráva.
  Když chodí moc zpráv, zvyš na 15–20.
- **costs** – tvoje průměrné náklady na auto (přepis, STK, čištění, rezerva).
- **keywords / exclude** – slova, která zprávu spustí, nebo inzerát vyřadí.

Nové hledání se při prvním běhu jen „naučí“ současné inzeráty, takže tě nezahltí.

## Volitelně – zhodnocení od Claude

1. Na **console.anthropic.com** si vytvoř API klíč a nabij kredit (stačí 5 $ na dlouho).
2. Přidej ho jako secret `ANTHROPIC_API_KEY`.
3. V `config.yaml` nastav `claude: enabled: true`.

Ke každé zprávě pak přibude krátké zhodnocení: rizika, odhad ceny a doporučení
(volat / zvážit / nevolat).

---

## Varianta B – běh na vlastním PC

Použij ji, když GitHub hlásí chyby **403** nebo **429** (weby blokují IP GitHubu).

1. Nainstaluj **Python 3.10+** z python.org (zaškrtni „Add Python to PATH“).
2. Do `config.yaml` doplň `telegram: token:` a `chat_id:`.
3. Ve složce s botem otevři příkazový řádek:
   ```
   pip install -r requirements.txt
   python bot.py --test
   python bot.py --loop 10
   ```
   `--test` pošle zkušební zprávu, `--loop 10` kontroluje každých 10 minut,
   dokud okno nezavřeš.

---

## Co dělat, když…

- **Nepřišla žádná zpráva:** otevři Actions → poslední běh → krok „Spustit bota“
  a podívej se na výpis. Ověř, že jsi botovi v Telegramu napsal jako první.
- **„Bazoš chyba“ nebo „Sauto chyba“:** web blokuje přístup nebo změnil strukturu.
  Druhý zdroj funguje dál. Zkus variantu B, případně se mi ozvi s chybovou hláškou.
- **Divné odhady:** u Bazoše se rok a km čtou z textu inzerátu, takže občas chybí.
  Odhad slouží jako filtr, co stojí za zavolání. Auto vždy prověř osobně.

**Poznámka:** Bazoš ani Sauto automatické procházení výslovně nepovolují. Při
osobním použití a intervalu 30 minut je zátěž zanedbatelná, ale interval nezkracuj.
