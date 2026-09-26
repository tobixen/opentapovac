# User review (Norwegian): Tapo RV50 Pro Omni, firmware 1.2.8

The author's review as posted to a product review site in September
2026 (2/5 stars).  Kept here as a local copy: it describes the problems
this project works around.

---

Jeg er ganske fornøyd med roboten i seg selv, men programvaren (firmware-versjon 1.2.8) fungerer så dårlig i vår bolig at jeg vurderte det til å være reklamasjonsgrunn. Det er ikke mulig å hacke firmware eller installere Valetudo. Jeg har fått AI-hjelp (Claude Code) til å late som at den er en telefonapp og ta over styringen - dette løser mange av problemene, men noen av problemene må løses gjennom endringer i firmware.

Første hinder var at produktet ikke fant trådløstnettet vårt, den støtter kun 2.4 GHz / WPA2. Jeg forsøkte først å bare trykke på knappen, og roboten begynte tilsynelatende å støvsuge, men tok ikke for seg hele rommet - man er nødt til å få støvsugeren online og registrere konto på en skyløsning og laste ned en app for å komme i gang. (Jeg har ikke undersøkt enda, men etter registrering tror jeg det er mulig å bruke roboten på lokalnettet uten bruk av skyløsning, og jeg tror ikke den sender detaljert kart etc ut av huset før man trykker på "backup"-knappen i app'en).

Det første problemet er plassering av basen. Idéelt sett ønsker man å plassere den i et hjørne eller en krok, kanskje mellom et skap og en kommode. Det fungerer ikke - det er nødvendig med en halv meter ledig plass til hver side, i tillegg til halvannen meter forover. Plassering i et hjørne eller nært et møbel medfører to problemer - roboten finner ikke basen - og når den skal starte støvsugingen, finner den ikke ut av hvor den er. (Dette bør jo være løsbart i programvaren - dersom basen ikke har vært flyttet så vet man jo eksakt posisjon).

Dørstokker er et stort problem med denne roboten. Vi har et par terskler som er akkurat litt for høye, den kommer seg over bare én vei. Roboten skjønner at det er umulig og krangler ikke med de høye tersklene. Skal man ha vasket på feil side, så må roboten løftes til riktig rom. Dersom roboten trenger å returnere til basen midtveis i vaskeprossessen og stopper ved en slik dørstokk, så avbrytes hele programmet.

Så har vi terskler som er lave nok til at roboten vil forsøke å passere begge veier. Roboten har en strategi - den går litt tilbake og tar fart. Når den får det til riktig (rett vinkel, og uten å krasje med dør, vegg eller annet), så fungerer det. Dette betyr at roboten er fysisk i stand til å komme seg over mange av dørstokkene våre. Dessverre er det umulig å markere dørstokkene på kartet, og roboten lærer heller ikke - så den bruker mye tid og batteri på å krangle med disse. App'en tilbyr knapper for kjør forover og snu, men ta fart og kom deg over dørstokken er tilsynelatende ikke en del av protokollen. Det som er virkelig ille er imidlertid en liten asymmetri: når roboten sendes ut for å vaske et rom, og må passere to lave terskler på veien, så greier den som regel å komme seg frem. På retur til basen bruker den tilsynelatende ikke gi fart-trikset, og kommer den seg ikke over på første forsøk så gir den opp og rapporterer at den ikke finner basen.

Roboten trenger støtt og stadig å finne posisjonen sin - typisk hver gang man løfter den over en dørstokk, hver gang den kommer ut fra basen sin, samt hver gang man ber den om å gå til en posisjon på kartet (noe som kan være nødvendig dersom man ikke er i huset og trenger å sende den hjem til basen sin). Før kartet var helt ferdigtegnet, så var det mye problemer, den rapporterte hyppig at den ikke fant posisjonen sin og kastet hele kartet (det så ut som at flytting av et møbel eller plassering av en ryggsekk eller musikkinstrument kunne være utslagsgivende), andre ganger rapporterte den feil posisjon og ødela dermed kartet.
