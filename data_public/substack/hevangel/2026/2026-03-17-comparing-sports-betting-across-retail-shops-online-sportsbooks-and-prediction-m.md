---
source: substack
channel: hevangel
channel_name: hevangel
external_id: '190569707'
url: https://hevangel.substack.com/p/comparing-sports-betting-across-retail
title: Comparing Sports Betting Across Retail Shops, Online Sportsbooks, and Prediction
  Markets
published_at: '2026-03-17T00:30:48.256000+00:00'
language: en
duration_sec: null
scraped_at: '2026-07-01T08:02:01.003535Z'
extra:
  audience: everyone
  slug: comparing-sports-betting-across-retail
  subdomain: hevangel
  wordcount: 3882
  rendered_fallback_used: false
  year: 2026
---

# Comparing Sports Betting Across Retail Shops, Online Sportsbooks, and Prediction Markets

- Publication: hevangel (hevangel)
- URL: https://hevangel.substack.com/p/comparing-sports-betting-across-retail
- Published: 2026-03-17
- Audience: everyone

ChatGPT Pro Deep Research

## **Executive summary**

Sports wagering now spans three distinct legal “venues”: land-based retail betting shops (or in-person sportsbooks), established online bookmakers/sportsbooks, and newer event‐contract / prediction-market platforms (including crypto-native markets such as Polymarket and decentralized protocols such as Augur). They all let a retail customer take economic exposure to sports outcomes, but they differ fundamentally in how prices are formed, what friction (vig/fees/spread) is paid, how quickly positions can be exited or settled, and which regulator governs the product.

Odds quality is best understood as the *all‑in cost of expressing a view* (embedded bookmaker margin/hold, explicit exchange fees, and bid–ask spread plus slippage). In regulated U.S. sportsbook markets, aggregated sportsbook “hold” rose to **10.16% in 2025** on **$166.94B** of legal handle (i.e., customers lost about 10 cents per dollar staked on average, before considering any individual bonuses), with **online representing 96.5%** of the market. DraftKings reported **Sportsbook Net Revenue Margin of 7.1% in 2025** (Sportsbook revenue as a percentage of handle, inclusive of promotional reinvestment effects per its KPI definition), and sportsbook handle of **$53.6B**. Prediction markets can, in some configurations, be structurally lower-cost: Polymarket states that **most markets are fee‑free** (no platform trading fees; it may enable taker fees on certain market types to fund maker rebates), and explicitly positions itself as a peer‑to‑peer order book rather than “the house.” Polymarket US (a CFTC‑regulated DCM) lists **0.10% taker fees** and **0% maker fees**, with free deposits/withdrawals on the platform. Kalshi’s fee schedule shows a probability‑dependent fee formula (illustratively **fees = round up(0.07 × C × P × (1−P))** for taker trades), plus no settlement or membership fee.

Market depth and choice strongly favor established online sportsbooks. In Great Britain, the Gambling Commission reported **5,825 betting shops** (declining) and **non‑remote betting GGY £2.5B**, while **remote betting GGY £2.6B**—showing that land-based and online are of comparable scale in revenue terms in that jurisdiction, though the product mix differs substantially. Online operators also advertise extremely high breadth: Entain’s annual report describes an “end-to-end product suite” and references **~40k betting events offered per week** (a proxy for breadth). In contrast, prediction markets typically list fewer contract types per sporting event than a modern sportsbook’s menu of spreads, totals, derivatives, prop markets, and Same‑Game Parlays.

For “ease of winning,” the key reality is that **negative expected value is the default** unless a customer has an informational or pricing edge that exceeds the all‑in cost of trading. Empirically, even the widely used “overround” calculation can understate how much bettors lose across available bet types when bookmakers price longshots with higher margins; Hegarty & Whelan (2025 draft) report that average loss rates across all available bets can be materially higher than implied by overround due to favorite‑longshot bias (e.g., about **one‑fifth higher** for their soccer dataset; **~40%** for tennis in their study design). Lower structural friction (low‑vig books, exchanges/prediction markets with tight spreads and low fees) generally improves the probability that a skilled retail player can be profitable, but it does not guarantee profitability and introduces other risks (liquidity, platform rules, resolution/oracle disputes, and legal access constraints).

Bottom-line conclusions (with caveats spelled out later):  
Better odds (lowest friction) tends to come from **(a)** low-vig online books and regulated exchanges where available, and **(b)** prediction markets **only when** the contract is liquid enough that spreads/slippage stay small and fees are low. More choices overwhelmingly come from **online sportsbooks**, with retail shops constrained by limited physical UX and a smaller menu, and prediction markets constrained by contract design and listing/approval processes. Higher long-run win probability for a retail player is most associated with **minimizing friction (vig/fees/spread)** and **maximizing the ability to line-shop/exit positions**, which usually favors **online** and **exchange-style** venues over retail shops.

## **Scope, definitions, and methodology**

This report compares three “sectors” that can all be “sports betting” from a consumer perspective:

Retail betting shops / land-based sportsbooks: physical venues where the bet is placed in person (counter, kiosk, or terminal). The operator is licensed under a gambling regulator (e.g., the UK Gambling Commission for Great Britain).

Established online bookmakers/sportsbooks: licensed online operators that set lines (bookmaking) and accept wagers via web/app, typically requiring account creation, KYC/age checks, and (in many jurisdictions) geolocation.

Prediction markets / event contracts: platforms where the primary product is a contract that settles to fixed value (often $1/$0) based on an event outcome, priced like a probability. Examples include Polymarket (crypto, order-book based, collateralized in USDC.e on Polygon) and Polymarket US (CFTC-regulated DCM), as well as Kalshi (CFTC-regulated exchange with a published fee schedule), and decentralized protocols such as Augur (Ethereum-based).

Approach to “odds quality”:  
For sportsbooks/bookmakers, “odds quality” is measured via (i) embedded margin in quoted prices (overround/vig) and (ii) realized hold/“net revenue margin” reported in filings or regulator/industry statistics. For prediction markets, “odds quality” is measured via (i) explicit fees and (ii) bid–ask spreads and slippage (which are liquidity-dependent). “Examples” use standard break-even math (e.g., -110 requires ~52.38% win rate to break even), combined with disclosed fee schedules where available.

Approach to financial metrics:  
Operator-level revenue/profit/EBITDA are taken from primary filings and annual reports where possible: Flutter FY2025 earnings release, DraftKings FY2025 10-K, Entain FY2024 annual report, bet365 FY ended 30 March 2025 Companies House accounts, and Caesars FY2025 press release. Market caps/tickers are inherently time-sensitive; values cited are “as of late Feb 2026” from snapshot sources (not audited).

## **Comparative analysis across key dimensions**

### **Side-by-side comparison table**

**DimensionRetail betting shops / land-based sportsbooksEstablished online sportsbooks/bookmakersPrediction markets / event contracts (e.g., Polymarket, Kalshi, Augur)**Price formationBookmaker sets odds; sometimes limited “price discovery” visible to bettorBookmaker sets odds, adjusts quickly; heavy analytics & tradingMarket price is a probability-like traded price; peers take other side; exchange operator typically does not “set” a house line Typical margin / “cost to bet”Embedded vig; often fewer competitors in-venue => weaker line shoppingEmbedded vig + promos; U.S. aggregate hold **10.16% (2025)**; DraftKings Sportsbook Net Revenue Margin **7.1% (2025)** Often explicit fees + spread. Polymarket: “vast majority” fee-free; certain market types have taker fees . Polymarket US: **0.10% taker / 0% maker** . Kalshi: fee formula-based, no settlement fee Liquidity / depthLiquidity concentrates on popular leagues; retail share can be small in mobile-led marketsVery high in major jurisdictions; U.S. handle **$166.94B (2025)**, online **96.5% share** Highly variable; liquid on headline events, thin on niche. Depth depends on market makers and participation; maker rebate programs exist explicitly to deepen liquidity Market breadthOften narrower menu than online due to space/time constraintsWidest menu: pregame, in-play, props, parlays, derivatives; Entain references ~**40k betting events/week** Usually fewer “contract types” per event; structure is question/contract driven (Yes/No or multi-outcome), though traders can enter/exit pre-resolution Settlement & withdrawal speedTicket settlement can be quick; cash payout depends on venue procedures (varies)In-app settlement is typically fast; cashout is gated by withdrawal processes. DraftKings and FanDuel both describe multi-step withdrawal processing and that banks may take “a few business days” after approval Settlement depends on resolution mechanics. Polymarket uses UMA Optimistic Oracle with dispute/challenge period ; UMA notes a “typical challenge period” of **two hours** . Kalshi lists no settlement fee; resolution timing depends on market rules Regulation & legal accessGoverned by local gambling regulator/licensing rules (e.g., UKGC) Governed by local gambling regulator; KYC/age checks and geolocation are standard in many regulated markets Often governed by financial-market regulation (CFTC in U.S. for DCMs). Polymarket US is listed by CFTC as a Designated Contract Market (designation date shown) . Polymarket previously faced CFTC action related to event-based binary options (2022) Retail player “ease of winning”Harder to systematically line-shop; smaller menu can reduce opportunity setEasier to line-shop across apps; but still negative EV absent edge. Hold/margins + bet-type mix (parlays/props) matter Potentially better if fees/spreads are low and pricing is efficient; but liquidity + execution quality + resolution risk matter; still negative EV without edge UXPhysical, cash-forward, immediate social/venue experienceBest-in-class app UX; fastest iteration; account + KYC frictionHybrid: crypto wallet/on-chain UX (Polymarket) vs regulated brokerage-like onboarding (Polymarket US/Kalshi). Polymarket US explicitly requires KYC before funding/trading

### **Odds quality and margin examples**

Two-outcome pricing example (sportsbook point spread moneyline –110/–110):  
A symmetrical –110/–110 line implies each side is priced at 52.38% implied probability; summed implied probability is 104.76%, which is an overround of 4.76%. This is the canonical “vig” story in U.S.-style spread markets, but realized hold can be materially higher depending on bet mix (parlays, in-play, same-game parlays, and mispriced longshots). DraftKings defines “hold” as settled handle minus payouts for resolved betting markets.

Realized margin proxies from filings and industry statistics:  
U.S. commercial sports betting hold was reported at **10.16% for 2025** in AGA’s national tracker, with **$16.96B** sports betting revenue and **$166.94B** handle. DraftKings reported **Sportsbook Net Revenue Margin 7.1% (2025)** and quarterly variation (e.g., 8.7% in Q2’25 and 5.2% in Q3’25), illustrating how bettor outcomes and promotional intensity change measured margins. Flutter’s earnings release highlights “structural revenue margin” dynamics and notes sportsbook revenue growth reflected a “net revenue margin” figure in context of U.S. results.

Prediction market pricing example (binary contract priced at $0.52):  
In Polymarket’s conceptual model, a share price is “approximately the probability.” If “Yes” trades at $0.65, the market implies roughly 65%. If a trader buys “Yes” at $0.52 and the contract settles to $1 on “Yes,” the break-even probability (before fees/spreads) is 52%. With Polymarket’s fee-free markets, the platform fee may be ~0, but spread/slippage still applies; with Polymarket US, a taker faces **0.10% fee** (10 bps), small compared with sportsbook vig on many bet types, but not always small compared with a tight exchange spread.

A crucial nuance: “overround” can understate expected losses across a menu of bets  
Academic work cautions that even “correctly” computed overround can mislead about the *average loss rate across available bets* when favorite-longshot bias exists. Hegarty & Whelan (revised April 2025 draft) argue that if bookmakers set higher profit margins for lower-probability outcomes, the equally weighted average loss rate across available bets can exceed the overround-based estimate; they report large differences in their soccer and tennis datasets. This matters most when a retail bettor shifts from low-margin core markets (major spreads/totals) into high-variance longshots and props.

### **Market depth and liquidity**

Retail is increasingly a distribution channel, not the liquidity center, in mobile-led jurisdictions  
The AGA reports that online sports betting represented **96.5% of the U.S. market in 2025**, implying that in-person retail took ~3.5% of sports betting revenue share in that measure. This is consistent with the modern U.S. bettor experience: most price discovery and volume is online, while retail provides branding, onboarding, and experiential value.

In Great Britain, land-based and remote remain comparable in revenue scale, but not identical in economics  
UKGC’s annual industry statistics (Apr 2024–Mar 2025) show **remote betting GGY of £2.6B** and **non-remote betting GGY of £2.5B**, alongside **5,825 betting shops**. This indicates that retail still matters materially in that jurisdiction, although a revenue number does not directly translate to liquidity or line quality; it does show that the number of physical venues and their revenue base remain significant.

Prediction-market liquidity is “spiky” and contract-dependent  
Polymarket explicitly frames liquidity depth as a driver of tighter spreads and more reliable fills and has instituted a maker rebates program funded by taker fees in selected market types to encourage “deeper liquidity and tighter spreads.” In practice, prediction markets often exhibit deep liquidity only where market makers and large trader communities concentrate (e.g., marquee sports events, elections), and can be thin elsewhere. This makes liquidity risk a first-order determinant of a retail trader’s realized execution cost.

### **Number of markets and choice per event**

Online sportsbooks dominate market breadth  
The online format supports a large, multi-layer menu: pregame, in-play, props, futures, and correlated parlays. Entain’s annual report highlights large scale and references a high cadence of betting opportunities (e.g., “40k betting events offered per week”), illustrating the breadth typical of major online operators.

Retail shops typically offer a curated subset  
A betting shop can offer a wide range, but physical constraints and staffing (and, in some jurisdictions, regulatory or operational policies) generally lead to fewer discoverable markets than an app that can surface hundreds of props per match.

Prediction markets offer fewer “market types,” but more flexibility in trading exposure  
Polymarket describes a model where users can buy/sell “Yes/No” shares on an order book and “exit anytime” by selling before resolution. That flexibility can substitute for sportsbook “cashout” features, but prediction markets typically do not match the combinatorial breadth of a sportsbook (e.g., same-game parlay builders and dense player prop menus).

### **Ease of winning for retail players: edge, variance, and practical constraints**

Expected value and the “cost hurdle”  
For a retail player, the path to long-run profitability requires beating the platform’s all-in friction (vig/fees/spread/slippage). Two structural points from primary sources illustrate why “winning is hard” even before we discuss skill:

Sportsbook outcomes are volatile and hold varies: DraftKings describes sports betting and iGaming as “characterized by an element of chance” and notes revenue variability with hold percentage fluctuations; a single large sporting event can impact short-term results. This same volatility also affects bettors, especially those using high-variance products.

Menu-weighted losses can exceed simple margin intuition: Hegarty & Whelan show that the commonly used overround approach can understate average losses across available bets when longshots carry higher implicit margins, making “betting around the board” worse than a bettor might assume.

Strategies that *reduce disadvantages* (not guarantees of profit)  
From a rigor standpoint, the only “strategy” that is universally beneficial is *minimizing friction* and *maximizing price optionality*: use line shopping across multiple regulated operators, avoid high-friction bet types unless you have demonstrable edge, and prefer venues where you can exit without punitive spreads/fees. The feasibility of this approach differs by venue:

Retail shops: line shopping is physically harder; selection is narrower; and the user is typically a price taker.

Online sportsbooks: line shopping is easiest; promos can reduce effective cost (but also steer users into higher-hold products), and the market breadth increases the temptation to bet into higher-margin niches.

Prediction markets: if liquid, they can offer low explicit fee schedules (Polymarket US 10 bps taker; Kalshi fee schedule clearly specified), but spreads in thin markets can dominate costs, and resolution mechanics introduce additional non-price risk.

## **Financial snapshot of representative operators**

### **Public and private operators by sector**

The same corporate groups often operate both retail and online (e.g., UK high-street brands paired with apps). This table therefore lists representative operators whose products map strongly to each “venue,” but the corporate financials can include multiple channels.

**Sector representationOperator (examples)Latest cited financial metricsTicker / Market cap (late Feb 2026 snapshot)Notes**Online sportsbook (global leader; also has some retail in certain markets)Flutter Entertainment (FanDuel, Paddy Power, Betfair, etc.)FY2025 revenue **$16.383B**, Adjusted EBITDA **$2.845B**, net loss **$407M** (earnings release). NYSE: **FLUT**; market cap snapshot available. Flutter also launched “FanDuel Predicts” (prediction-style product) per its release, illustrating convergence. Online sportsbook (U.S.-focused)DraftKingsFY2025 revenue **$6.055B**, net income **$3.7M**, Adjusted EBITDA **$620.0M**. Sportsbook handle **$53.6B**, Sportsbook Net Revenue Margin **7.1%**. NASDAQ: **DKNG**; market cap snapshot available. Filing defines hold and highlights hold volatility as a business factor. Retail + online (UK/EU footprint; includes high street brands)Entain (Ladbrokes, Coral, bwin, etc.)FY2024 group revenue **£5.1B**, underlying EBITDA **£1,089M**, Online Net Gaming Revenue **£3.7B** (annual report highlights). LSE: **ENT**; market cap snapshot available. Annual report also references BetMGM “gaming revenue” **$2.1B** (100% basis in highlight box). Online sportsbook (large private operator)bet365FY ended 30 Mar 2025 turnover **£4.0419B**; profit before tax **£338.5M**; profit for period **£249.7M** (Companies House accounts). PrivateAccounts show scale comparable to major publics (but no public market cap). Retail casino/sportsbook operator with major digital segmentCaesars EntertainmentFY2025 GAAP net revenues **$11.5B**; Same-store Adjusted EBITDA **$3.6B**; Caesars Digital Adjusted EBITDA **$236M** (press release). NASDAQ: **CZR**; market cap snapshot available. Illustrates that “retail-heavy” groups increasingly report digital profitability separately. Prediction markets (exchange model, regulated)Polymarket US (QCX d/b/a Polymarket US), Kalshi (examples)Polymarket US: taker **0.10%**, makers **0%**, deposits/withdrawals free on-platform. Kalshi: fee formula-based; no settlement fee. PrivateThese are not sportsbooks; revenues are transaction-fee driven and generally not publicly disclosed at the platform level. Prediction market protocol (decentralized)Augur (protocol)Research/whitepaper describes decentralized oracle/prediction market settlement incentives. Token exists (not equity)Protocol activity and “fees” are on-chain and variable; TVL snapshots (e.g., DeFi analytics) suggest much smaller scale than top centralized venues.

### **Sector-level “size” signals from regulators/industry bodies**

Great Britain (regulator view): Total customer-facing gambling GGY **£16.8B** (Apr 2024–Mar 2025); land-based sectors **£4.8B**; Remote Casino/Betting/Bingo **£7.8B**; betting shops **5,825**; non-remote betting GGY **£2.5B**; remote betting GGY **£2.6B**.

United States (industry body view): 2025 sports betting revenue **$16.96B**, sports betting handle **$166.94B**, and national hold **10.16%**, with online comprising **96.5% share**.

These sector statistics are not directly comparable across countries (different tax/definitions/product mixes) but are useful for scale and channel share.

## **Shared infrastructure and vendor ecosystem**

Despite differences in pricing and regulation, the three venue types often rely on overlapping “plumbing”:

Sports data & odds feeds (inputs to pricing, settlement references, and integrity):  
Sportradar positions itself “at the intersection between sports, media and betting,” reflecting its multi-vertical role. FIFA announced Stats Perform as its official worldwide betting data and streaming rights distributor, illustrating how official data rights underpin betting ecosystems.

Sportsbook platforms & trading/risk engines (especially important for omnichannel retail+online):  
Kambi markets itself as an omni-channel sportsbook partner, explicitly discussing plans that include retail. Scientific Games describes sports betting systems that integrate with “land-based retail systems” and “licensed, digital sports betting platforms.” IGT’s PlaySports is presented as a sports betting supplier/platform supporting sportsbook operations. These vendors illustrate how retail and online are often implemented on the same core trading stack.

Payments, identity/KYC, and geolocation (compliance backbone across regulated venues):  
GeoComply markets geolocation compliance for gaming and lists major operators (FanDuel, DraftKings, Caesars, BetMGM, etc.) as customers/partners, demonstrating cross-operator standardization of tooling in regulated online betting. FanDuel describes its KYC/age check process for account creation (DOB/SSN in relevant states; underage blocking), while DraftKings notes it uses third-party identity verification vendors, showing how KYC is outsourced across major sportsbooks. Location verification can include GeoComply-style plugins and mobile location services in online betting contexts.

Blockchain, stablecoins, and oracle infrastructure (prediction-market specific, but increasingly “institutional grade”):  
Polymarket describes collateralization in **USDC.e (bridged USDC on Polygon)**, tokenized “Yes/No” shares, and resolution via **UMA Optimistic Oracle**. Circle announced a partnership with Polymarket to transition from bridged USDC on Polygon to **native USDC** in coming months, illustrating the “stablecoin settlement layer” that prediction markets increasingly depend on. UMA documentation explains “liveness”/challenge periods and notes that a “typical challenge period is two hours,” a key tradeoff between security and UX for oracle-based settlement.

### **Market relationships flowchart (venue and infrastructure interactions)**

```
mermaid
```

**Copy**

```
flowchart TD
  A[Sports events & outcomes<br/>(leagues, matches, stats)] --> B[Official/market data<br/>+ integrity feeds]
  B --> C[Bookmakers & sportsbooks<br/>(retail + online)]
  B --> D[Prediction markets<br/>(event contracts)]
  
  C --> E[Retail betting shop / casino sportsbook]
  C --> F[Online sportsbook apps/web]
  
  D --> G[Crypto-native markets<br/>(on-chain collateral, order book)]
  D --> H[CFTC-regulated exchanges<br/>(DCM rulebooks, clearing)]
  
  I[KYC / Age verification] --> C
  I --> D
  J[Geolocation compliance] --> F
  K[Payments & rails<br/>(cards/ACH/e-wallets/stablecoins)] --> C
  K --> D
  
  L[Settlement mechanism]
  L --> C
  L --> D
  
  M[Regulators]
  M --> E
  M --> F
  M --> H
```

This diagram reflects that the *same upstream data and compliance vendors* can serve both sportsbooks and prediction markets, even though their settlement and pricing models differ.

## **Conclusions and explicit assumptions**

### **Which venue offers better odds?**

Better odds (lowest expected friction) are most often found where **competition is highest** and **pricing is most transparent**, which typically favors online and exchange-style venues over retail-only environments.

Online sportsbooks provide the best “mainstream competitive pricing” *when you can line-shop*: U.S. market-wide data show high realized hold (10.16% in 2025), but that is an average across products and includes outcomes that can be especially bookmaker-favorable in certain periods. Operator filings show meaningful variation in net revenue margin (DraftKings 7.1% Sportsbook Net Revenue Margin in 2025; significant intra-year swings).

Prediction markets can be structurally lower-fee *on paper*: Polymarket states most markets are fee-free and uses a peer-to-peer order book. Polymarket US lists only 10 bps taker fees. Kalshi’s fee schedule is explicit and probability-dependent.   
However, in practice the “odds quality” for a retail participant depends on **bid–ask spread and slippage**, which can dominate the cost in thin markets—hence Polymarket’s stated rationale for maker rebates to deepen liquidity and tighten spreads.

Retail shops are usually weakest on odds quality for a price-sensitive bettor: they are convenient and may offer cash-centric UX, but the ability to line-shop is limited and the discoverable market menu is typically smaller. UKGC statistics show retail remains large in GB, yet the structural advantage for odds shopping still tends to be online.

### **Which venue offers more choices?**

Online sportsbooks offer the most choices by a wide margin. They can list massive numbers of events and micro-markets; Entain references ~40k betting events per week as one signal of that breadth. Retail venues often mirror a subset of the online menu. Prediction markets generally list fewer distinct contracts per game (though they may allow trading in/out), because each contract must be defined and resolved under a rule set.

### **Which venue offers higher win probability for retail players?**

If “win probability” means “probability of being profitable over time,” the dominant driver is whether the player can sustain an edge **greater than** the venue’s all‑in friction:

Lower friction raises the ceiling for profitability: e.g., a sportsbook –110 standard requires ~52.38% hit rate to break even, while a lower-cost venue (or a lower-fee/fee-free traded contract) can lower the break-even threshold. Polymarket US’s 10 bps taker fee is small enough that, in a liquid contract with tight spreads, the break-even probability approaches the traded price itself.

But product choice matters more than many retail bettors realize: academic evidence shows that simple margin intuition based on overround can understate average losses across “available bets” if longshots/low-probability bets embed higher margins (favorite-longshot bias), making casual diversification into high-variance bets structurally worse.

Execution and settlement risks differ: prediction markets introduce “oracle/rulebook resolution” risk and may impose dispute windows; Polymarket uses UMA’s optimistic oracle, and UMA notes a typical two-hour challenge period (longer if matters are disputed/escalated). Sportsbooks introduce withdrawal processing and operator risk controls, but settlement is usually operationally straightforward once results are official; withdrawal timing can still take days depending on rails (DraftKings/FanDuel support pages).

### **Key assumptions and unspecified items**

No single “average vig” applies universally. Sportsbooks’ realized hold depends on sport mix, bet types (parlays vs straight bets), promotional reinvestment, customer skill distribution, and luck. The best primary proxy we used for the U.S. was AGA’s national hold (10.16% for 2025) and DraftKings’ disclosed Sportsbook Net Revenue Margin (7.1% for 2025).

Prediction market “odds quality” is contract-specific. This report cites published fee schedules and the presence/absence of platform fees, but it does not compute live spreads/slippage because those require time-stamped market snapshots and/or proprietary datasets. Fee schedules were taken from Polymarket/Polymarket US/Kalshi documentation.

Legal access is jurisdiction-specific and evolving. In the U.S., event contracts implicate CFTC oversight, and Polymarket US appears as a designated DCM in CFTC industry filings; Polymarket previously faced a CFTC enforcement action in 2022. In gambling-regulated jurisdictions (e.g., Great Britain), the UKGC’s licensing and reporting regime frames market structure and published sector statistics.

If you interpret “retail betting shops” narrowly as only UK-style high-street bookmakers, jurisdictional conclusions may differ from U.S. casino sportsbook retail; this report uses UKGC and U.S. AGA data to illustrate two major regulated models.
