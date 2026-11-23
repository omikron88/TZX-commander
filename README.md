# TZX Commander

Dvoupanelový terminálový správce obrazů kazet ZX Spectrum ve formátech TAP a TZX.
Každý panel může zobrazit adresář nebo obsah otevřeného obrazu. Pro čtení a zápis
kazetových bloků správce používá knihovnu z balíku [tzxtools](https://github.com/shred/tzxtools).

## Instalace

Vyžaduje Python 3.10 nebo novější a interaktivní terminál.

### Windows (PowerShell)

```powershell
git clone https://github.com/omikron88/TZX-commander.git
cd TZX-commander
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\tzx-commander.exe
```

Pokud už máte klon, aktualizujte ho příkazem `git pull` před spuštěním instalace.

### Linux a macOS

```sh
git clone https://github.com/omikron88/TZX-commander.git
cd TZX-commander
python3 -m venv .venv
source .venv/bin/activate
python -m pip install .
tzx-commander
```

Levý a pravý panel můžete otevřít přímo zadáním dvou adresářů nebo obrazů.

```sh
tzx-commander "kazety/hry.tap" "kazety/kompilace.tzx"
```

## Ovládání

| Klávesa | Akce |
| --- | --- |
| Tab / Shift+Tab | Přepne aktivní panel. |
| Šipky, PgUp/PgDn, Home/End | Posunou výběr. |
| Enter | Otevře adresář nebo obraz. U bloku otevře jeho detail. |
| Backspace | Zavře obraz nebo přejde do nadřazeného adresáře. |
| Mezera / Insert | Označí jednotlivý blok. |
| P | Označí odpovídající dvojici hlavička a data, pokud sousedí. |
| F1 | Zobrazí nápovědu. |
| F2 | Otevře cestu zadanou ručně. |
| F3 | Zobrazí seznam bloků obrazu nebo detail bloku s hexadecimálním náhledem. |
| B | Zobrazí listing programu ZX Spectrum BASIC. |
| A | Zobrazí disassembler Z80 pro blok `CODE` se známou adresou. |
| V | Otevře náhled obrazovky ZX Spectrum z panelu nebo detailu bloku. |
| F4 | Uloží binární obsah aktuálního bloku. Obrazovku SCREEN$ s odpovídající hlavičkou nabídne jako `.scr`. |
| F5 | Kopíruje obraz, přidá bloky do otevřeného cílového obrazu nebo je uloží jako nový obraz. |
| F6 | Přesune či přejmenuje soubor obrazu. Pro přejmenování ve stejném adresáři lze zadat úplnou cílovou cestu. |
| F7 | Vytvoří adresář nebo prázdný obraz TAP či TZX. |
| F8 | Po potvrzení smaže vybrané bloky nebo celý soubor obrazu. |
| F9 | Převede celý obraz mezi TAP a TZX. |
| F10 / Esc | Ukončí program. |
| Ctrl+R | Znovu načte oba panely. |

Pro kopírování programu otevřete zdroj vlevo a cíl vpravo. Vlevo najděte
hlavičku nebo datový blok programu, stiskněte `P` a potom `F5`. Správce přidá
označené bloky v jejich původním pořadí na konec cílového obrazu. Pokud je
napravo adresář, nabídne uložení do nového souboru. V panelu bez označení se
kopírování a mazání bloků týká aktuálního řádku.

## Listing BASICu a disassembler Z80

Klávesy `B` a `A` fungují na hlavičce i na odpovídajícím datovém bloku přímo
v panelu nebo v detailu bloku (`F3`). Listing BASICu zobrazuje příkazy
s čísly řádků a končí před uloženými proměnnými. Disassembler uvádí hexadecimální
adresu, bajty a instrukci Z80. Počáteční adresu bere z hlavičky `CODE` (`Bytes`).

Dlouhé řádky lze posouvat šipkami doleva a doprava. Klávesa `Esc` se vrátí
na předchozí obrazovku správce.

## Náhled obrazovky ZX Spectrum

V otevřeném obrazu kazety vyberte blok a stiskněte `V`. Stejná klávesa funguje
v detailu bloku (`F3`). Náhled je dostupný na hlavičce `CODE` (`Bytes`)
i na odpovídajícím datovém bloku, pokud zápis zasahuje do obrazové paměti
`0x4000–0x5AFF`. U částečného zápisu se data vykreslí na své adrese
v prázdné obrazovce. U bloku `CODE` mimo obrazovou paměť (například na
adrese `0x8000`) nebo u bezhlavičkového bloku se přesně 6912 bajtů dat
zobrazí jako celá obrazovka.

Okno otevírá prohlížeč z adresáře `tools/`, který vyžaduje grafické prostředí
a Tkinter. Dostupnost Tkinteru lze ověřit příkazem `python -m tkinter`.
Klávesa `Esc` nebo mezera zavře okno a vrátí vás do správce.

## Formáty a omezení

- TAP uchovává standardní datové bloky. Při uložení TZX do TAP správce požádá
  o potvrzení, pokud musí vypustit metadata nebo nahradit délky pauz. Obraz
  s nestandardními signálovými bloky, například turbo záznamem, převod zastaví.
- Správce odmítne úpravy, které by mohly porušit skupiny, smyčky nebo skoky
  v TZX. Celé obrazy lze mezi adresáři kopírovat bajt po bajtu.
- Nové výstupy vyžadují volný název. Při přidávání nebo mazání bloků se změna
  zapíše do dočasného souboru a obraz se nahradí až po úspěšném zápisu.
- Podporované typy bloků TZX odpovídají knihovně tzxtools 1.9.5.

## Vývoj

```sh
python -m unittest discover -s tests -v
```

Správce `tzx_commander` a jeho testy jsou pod licencí [GPL-3.0-or-later](LICENSES/GPL-3.0-or-later.txt).
Pro původní nástroje v `tools/` viz [LICENSE](LICENSE).
