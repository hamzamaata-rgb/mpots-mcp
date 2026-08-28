# Note méthodologique — loyers résidentiels à Casablanca

Étude du marché locatif résidentiel casablancais à partir d'annonces en ligne : loyer
médian au m² par quartier et typologie, dispersion, évolution à qualité constante, proxy
de tension locative.

> **État au 28 août 2026 : aucune donnée n'a encore été collectée.** La chaîne de collecte,
> de normalisation et d'analyse est écrite et testée (144 tests), mais l'environnement
> d'exécution utilisé pour le développement bloque l'accès réseau sortant. Toutes les
> sections marquées « à remplir après collecte » contiennent la commande qui produit le
> chiffre, jamais une estimation. Aucun effectif, aucune médiane et aucun indice de ce
> document n'est un ordre de grandeur supposé.

---

## 1. Sources

| Source | Rôle | Nature |
|---|---|---|
| Avito.ma | Source principale, collecte courante (volet B) | Annonces de location, relevé quotidien |
| Wayback Machine (CDX) | Historique (volet A) | Captures d'archives d'annonces Avito, `source = 'avito_wayback'` |
| Mubawab.ma | Source secondaire éventuelle | Non implémentée à ce stade |
| HCP — IPC Casablanca, composante loyers | Garde-fou externe, mensuel depuis 2007 | Enquête auprès d'environ 1 425 locataires à Casablanca |
| Bank Al-Maghrib / ANCFCC — IPAI Casablanca | Garde-fou externe, trimestriel | Indice des prix des actifs immobiliers (prix de vente) |

Les deux dernières séries ne sont pas collectées automatiquement : elles se récupèrent à
la publication et s'alimentent à la main dans le notebook `indice.ipynb`.

### Conduite de la collecte

Une requête à la fois, jamais de parallélisme, 3 à 5 secondes d'attente entre deux
requêtes, user-agent identifiable et non déguisé en navigateur, `robots.txt` lu et
respecté avant la première requête. Un HTTP 403 ou 429 arrête la collecte immédiatement
et l'inscrit dans la table `runs` : ni nouvelle tentative, ni rotation d'adresse, ni
contournement d'anti-bot. Aucune donnée personnelle n'est stockée — le schéma ne comporte
pas de colonne de contact, et les téléphones, emails et URLs présents dans les textes
libres sont remplacés par des marqueurs avant écriture en base. Seul un booléen `is_pro`
(agence vs particulier) est conservé.

## 2. Période et effectifs

*À remplir après collecte —* `python analyse.py` *et* `python db.py --stats`.

| Indicateur | Valeur |
|---|---|
| Première observation | — |
| Dernière observation | — |
| Annonces uniques collectées | — |
| Annonces valides (`qualite >= 2`, hors doublons et exclusions) | — |
| Cellules quartier × typologie publiables (n ≥ 30) | — |
| Cellules avec IC crédible (n ≥ 80) | — |

## 3. Règles d'exclusion

Appliquées à la normalisation (`normalize.py`), toutes tracées : une ligne rejetée est
conservée en base avec son motif, jamais supprimée silencieusement.

| Règle | Seuil | Motif enregistré |
|---|---|---|
| Surface hors bornes | < 15 m² ou > 500 m² | surface à `NULL`, `qualite = 0` |
| Loyer sous plancher | < 1 000 MAD/mois | `sous_plancher` |
| Loyer au-dessus du plafond | > 100 000 MAD/mois | `vente` — annonce de vente mal catégorisée |
| Vocabulaire de vente | « à vendre », « prix de vente », « cession » | `vente` |
| Location courte durée | prix à la nuit, à la journée, à la semaine | `courte_duree` |
| Quartier non rattaché | aucun alias ni fuzzy match concluant | `qualite = 0`, libellé versé dans `unmatched_quartiers.csv` |
| Doublon | même quartier, surface ±3 m², loyer ±5 %, similarité de description > 0,85 | `duplicate_of` renseigné, ligne conservée |
| Hors commune | Bouskoura, Dar Bouazza, Errahma, Nouaceur, Mohammedia, Tit Mellil | `perimetre = 'peripherie'`, exclu par défaut |

**Score de qualité.** 3 = surface lue dans un champ structuré, quartier rattaché
exactement, loyer présent ; 2 = quartier issu d'un fuzzy match ; 1 = surface extraite du
texte libre ; 0 = au moins un des trois champs manquant. Les dégradations se cumulent par
le minimum. **Toutes les analyses filtrent sur `qualite >= 2`.**

Ce plafond a une conséquence à surveiller : si Avito n'expose pas de champ surface
structuré dans ses pages de résultats, toute surface est extraite du texte, toute ligne
tombe à 1, et le filtre vide l'échantillon. Le point se tranche sur données réelles
(constante `CAP_SURFACE_TEXTE` dans `normalize.py`) et la décision doit être consignée ici.

## 4. Design d'échantillonnage

**Unité d'analyse : quartier × typologie** (studio/T2, T3, T4+). Ce qui compte n'est pas
le volume total mais l'effectif par cellule.

- **n ≥ 30** avant de publier une médiane. Sous ce seuil, `stats_par_cellule` conserve la
  cellule et son effectif mais masque la statistique : on voit qu'elle existe et pourquoi
  elle est vide, plutôt que de lire une médiane calculée sur quatre annonces.
- **n ≥ 80** pour un intervalle de confiance crédible.
- Le référentiel compte 41 quartiers dans la commune, soit 123 cellules. **La majorité
  restera sous le seuil**, quel que soit le volume collecté : c'est une propriété du
  découpage, pas un échec de la collecte. Le repli prévu est l'agrégation au segment
  (`stats_par_segment`) ou à l'arrondissement, et il doit être annoncé, pas subi.
- **Pondération : aucune par défaut.** Les résultats principaux sont bruts par cellule.
  Une version pondérée par le poids démographique des arrondissements figure en annexe
  seulement, et n'est calculable qu'une fois `poids_arrondissements.csv` renseigné à la
  main depuis les données du RGPH ; la fonction refuse de calculer sur des poids partiels
  plutôt que d'inventer une population.

## 5. Biais connus

À documenter, pas à corriger silencieusement.

**Composition de la source.** Avito sur-représente le segment intermédiaire et les
particuliers. Le haut de gamme casablancais passe majoritairement par des agences et des
réseaux fermés qui n'annoncent pas, ou annoncent sans prix. Les niveaux mesurés sur Anfa,
Aïn Diab, Californie et le Triangle d'Or sont donc à lire comme la partie visible et
probablement la moins chère de ces marchés.

**Prix demandé ≠ prix de transaction.** Le loyer affiché est une demande initiale. L'écart
à la négociation est typiquement de 5 à 15 % à la baisse. Aucun coefficient correcteur
n'est appliqué : les résultats sont des loyers demandés et doivent être nommés ainsi.

**Charges.** `charges_incluses` n'est renseigné que lorsque l'annonce le précise. Le champ
est majoritairement `NULL`, ce qui introduit une hétérogénéité non contrôlée entre annonces
charges comprises et hors charges — de l'ordre de quelques centaines de dirhams par mois
en collectif.

**Meublé.** Détecté par mots-clés (français, arabe, anglais) à défaut de champ dédié. La
négation est traitée (« non meublé », « غير مفروش »), mais un silence n'est pas un « non » :
le champ reste `NULL` quand rien n'est dit, et le meublé se paie nettement plus cher.

**Captures Wayback (volet A).** Échantillon de convenance : une page est archivée parce
qu'elle a été consultée ou soumise, pas par tirage. La composition par quartier et par
gamme y est arbitraire et varie d'une année à l'autre. **À n'utiliser que pour la tendance
relative dans le temps, jamais pour un niveau absolu.**

**Censure des durées.** La durée de mise en ligne n'est complète que pour les annonces
passées à `disparue`. Les annonces encore actives sont censurées à droite et sont exclues
du calcul, ce qui biaise la médiane vers le bas tant que la fenêtre d'observation est
courte. L'indicateur n'est pas calculé avant deux trimestres de collecte, et la fonction
`durees_exploitables` refuse explicitement de le valider avant.

**Disparition ≠ location.** Une annonce disparue peut avoir été louée, retirée, expirée ou
republiée sous une nouvelle URL. Le dédoublonnage rattrape une partie des republications,
pas toutes. La durée de mise en ligne est donc un proxy de tension, pas une mesure de délai
de location.

## 6. Mesure de l'évolution — indice hédonique

**Le piège.** Comparer la médiane brute de deux périodes ne mesure pas l'évolution des
loyers, mais surtout le changement de composition de l'échantillon. Si les captures de 2021
sur-représentent Anfa et celles de 2026 Bernoussi, l'indice baisse sans qu'aucun loyer
n'ait bougé. Sur des données d'annonces, ce biais domine presque toujours le signal.

**Spécification retenue.** MCO sur les annonces valides (`qualite >= 2`), avec statsmodels :

```
log(loyer_mad) ~ log(surface_m2) + nb_pieces + meuble + etage
                 + C(quartier_norm) + C(periode)
```

Les coefficients des indicatrices de période, exponentiés et rebasés à 100 sur la première
période, **sont** l'indice des loyers. Le contrôle par les indicatrices de quartier et par
les caractéristiques du bien neutralise le changement de mix par construction.

**Conditions d'application, à vérifier avant toute publication :**

1. au moins 100 observations valides par période, sinon agréger (trimestre → semestre → année) ;
2. au moins 3 périodes, sinon ce n'est pas une série ;
3. reporter systématiquement les intervalles de confiance des coefficients de période et le
   n par période — sur petit échantillon l'IC sera large, et c'est une information, pas un échec ;
4. produire la version naïve (médiane brute par période) **à côté**, pour montrer l'écart
   entre les deux et justifier la méthode.

**Garde-fou externe.** L'indice hédonique se compare à la composante loyers de l'IPC du HCP
pour Casablanca. Attention à ce que mesure cette série : c'est un indice de loyers **en
cours** — le stock de baux existants, alimenté par un questionnaire auprès d'environ 1 425
locataires — et non de loyers **de marché**, c'est-à-dire des nouveaux baux que reflètent
les annonces. Elle bouge donc plus lentement et plus doucement. Un écart de quelques points
par an est normal ; au-delà, suspecter un artefact d'échantillon avant de conclure à un
retournement de marché.

L'IPAI de Bank Al-Maghrib / ANCFCC (trimestriel, prix de vente) est suivi en parallèle. Le
rapport des deux séries donne l'évolution du **rendement locatif brut**, qui est la variable
d'intérêt finale de l'étude.

## 7. Reproductibilité

```bash
python db.py --init                  # schéma + référentiel des quartiers
python collect.py --probe            # calibration du parser sur une page réelle
python collect.py --pages 5          # collecte quotidienne
python dedup.py                      # rattachement des republications
python analyse.py                    # couverture et cellules sous le seuil
```

La collecte est idempotente : deux passes le même jour ne créent ni doublon d'annonce ni
doublon de snapshot, et `first_seen` n'est jamais réécrit. Chaque exécution laisse une ligne
dans `runs` — y compris les arrêts sur refus du site et les rejeux de fichiers — de sorte
que l'origine de chaque ligne de la base reste traçable après coup.

Le référentiel des quartiers a pour source de vérité `quartiers_seed.csv`, versionné et
relisible en diff ; `python db.py --sync-quartiers` le réapplique après révision manuelle.
Les libellés de lieu non rattachés s'accumulent avec leur fréquence dans
`unmatched_quartiers.csv` et se traitent à la main, en alias ou en nouveau quartier.
