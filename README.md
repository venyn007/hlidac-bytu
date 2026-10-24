# Hlídač pronájmů

Samostatný program pro GitHub Actions, bez placené AI nebo vyhledávacího API. Kontroly v 9:00, 14:00 a 19:00 Europe/Prague; plánovač GitHubu může mít zpoždění. Mac nemusí běžet.

## Parametry

- Letná, Bubeneč a okolí Hradčanské: přibližný obdélník podle mapy a seznam ulic v `config.json`, hraniční adresy k ověření.
- Alespoň 100 m² a dispozice 3+kk, 3+1, větší nebo atypická.
- Strop 70 000 Kč/měsíc včetně poplatků a energií. Pokud už známé náklady překročí strop, nabídka se vyřadí. Nevyčíslené energie se nepočítají jako nula: všechny odesílané nabídky jsou označené **k ověření**.
- Půdorysy a fotografie se neposuzují. Z textu se hledá zmínka o pracovně, hale nebo galerii; dvě ložnice a prostor pro práci nejsou automaticky potvrzené.
- Vybavení nerozhoduje. Kauce a provize nejsou měsíční náklady.

## Zapojení

V Settings → Secrets and variables → Actions přidej:

- `CALLMEBOT_PHONE`: WhatsApp číslo s předvolbou, bez plus a mezer.
- `CALLMEBOT_APIKEY`: klíč od CallMeBotu.

Klíč ani číslo nepatří do souborů, issue nebo chatu. Potom v Actions spusť „Hlídání bytů“ → Run workflow s volbou testovací zprávy. Po potvrzení doručení spusť plnou kontrolu. Sleduj první běh: dostupnost webů z GitHubu se může lišit od domácí sítě.

## Průhlednost a omezení

Program načítá veřejné výpisy a detaily čtyř webů. Neobchází přihlášení, CAPTCHA ani blokace. Prázdné či změněné rozhraní a neúplné stránkování hlásí chybu běhu, nikoli úspěšnou kontrolu bez nálezů. Chyby a kandidáti jsou v přehledu běhu a v sedmidenním reportu. Zapni si GitHub oznámení o neúspěšných Actions bězích. Žádný automatický scraping nezaručuje úplné pokrytí portálů.

Identifikátory dříve oznámených bytů jsou v `seed.json`. `state.json` uchovává identifikátory, ceny a otisky pro deduplikaci, nikoli klíče nebo telefon. Pro shodu napříč weby se vyžaduje stejná ulice, velikost, dispozice a výrazně podobný text; jinak se byty neslučují naslepo. Nejasné odeslání CallMeBotem se automaticky neopakuje, aby nevznikaly duplicitní zprávy. Úspěch API znamená přijetí službou, nikoli doručení telefonu.

Standardní GitHub Actions ve veřejném repozitáři jsou v bezplatném režimu; nepoužívej větší placené runnery. Veřejné repozitáře mohou mít plán po delší neaktivitě vypnutý. Kontroluj občas historii běhů. Program nepřechází na placenou službu a nepotřebuje kartu.

Lokální kontrola bez zpráv: `python watch.py --dry-run`. Test filtrů: `python -m unittest -v`.
