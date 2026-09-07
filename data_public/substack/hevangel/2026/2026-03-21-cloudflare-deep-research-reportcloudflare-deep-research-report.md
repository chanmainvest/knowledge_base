---
source: substack
channel: hevangel
channel_name: hevangel
external_id: '190569797'
url: https://hevangel.substack.com/p/cloudflare-deep-research-reportcloudflare
title: Cloudflare Deep Research ReportCloudflare Deep Research Report
published_at: '2026-03-21T00:33:13.516000+00:00'
language: en
duration_sec: null
scraped_at: '2026-07-01T08:01:57.061196Z'
extra:
  audience: everyone
  slug: cloudflare-deep-research-reportcloudflare
  subdomain: hevangel
  wordcount: 4701
  rendered_fallback_used: false
  year: 2026
---

# Cloudflare Deep Research ReportCloudflare Deep Research Report

- Publication: hevangel (hevangel)
- URL: https://hevangel.substack.com/p/cloudflare-deep-research-reportcloudflare
- Published: 2026-03-21
- Audience: everyone

ChatGPT Pro Deep Research

## **Executive summary**

Cloudflare has evolved from a reverse-proxy CDN and DNS provider into a broad “connectivity cloud” platform that blends application delivery (CDN/cache, DNS, load balancing) with security (WAF/WAAP, bot management, DDoS), developer compute (Workers, Workers AI), data services (R2 object storage and an emerging analytics stack), and enterprise network security (Zero Trust / SASE, Magic Transit). Its technical strategy is to run a consistent service stack close to users across a very large Anycast edge network, with centralized configuration (“control plane”) and a globally distributed traffic-processing layer (“data plane”). Cloudflare’s own resilience documentation explicitly frames the architecture as a control plane + data plane separation, where policies are replicated to the edge so the data plane can remain operational even if connectivity to the control plane is impaired.

In financial terms, Cloudflare delivered FY2025 revenue of about $2.168B (+30% YoY) and Q4’25 revenue of $614.5M (+34% YoY), with FY2025 non-GAAP gross margin ~75.8% and FY2025 free cash flow of ~$260.6M (~12.0% margin). Its growth has become increasingly enterprise-weighted: in Q4’25 it reported 4,298 customers paying >$100k annually (up 23% YoY) contributing 73% of revenue, alongside ~332k paying customers total and a dollar-based net retention rate of 120%. Cloudflare’s remaining performance obligations (RPO) ended Q4’25 at $2.496B (+48% YoY), suggesting strong forward demand and multi-year commitments.

Cloudflare’s moat rests on (a) edge-network scale and Anycast routing that spreads load and mitigates attacks, (b) a unified platform that reduces point-solution sprawl for buyers, (c) ecosystem/PLG distribution via millions of Internet properties and a large developer surface area, and (d) expanding switching costs as customers adopt more “stateful” edge services (Workers, tunnels, Zero Trust, network routing). However, vulnerabilities include: platform-wide outage blast radius from shared control systems and automation, competitive pressure from hyperscalers bundling adjacent services, and regulatory/geopolitical constraints (including Cloudflare’s disclosed China-network dependency on JD Cloud for an “integrated global network that includes China”).

AI is a major strategic accelerant and risk vector. Cloudflare is positioning itself as infrastructure for “agentic” workloads and AI-era traffic patterns, while building AI-native products (Workers AI, AI Gateway, Firewall for AI, agent frameworks) and governance controls (default blocking/permissioning of AI crawlers; “Pay Per Crawl”; “AI Labyrinth” bot traps). The opportunity is inference and orchestration “at the edge” plus security/observability for AI apps; the risk is commoditization and fast-follow competition from hyperscalers and other edge providers.

## **Technology and architecture**

### **Core products and what they do**

Cloudflare’s application delivery layer is fundamentally a globally distributed cache/CDN capability that stores copies of content (images, videos, web pages) in geographically distributed data centers closer to users, improving latency and reducing origin load. It exposes fine-grained controls such as Cache Rules and default caching behavior, allowing customers to define what is eligible for caching and for how long. A notable performance enhancer is Tiered Cache, which uses the size of Cloudflare’s network to increase cache hit ratios and reduce origin fetches by introducing a hierarchy between “lower-tier” edges and “upper-tier” cache nodes.

Cloudflare DNS includes authoritative DNS (for domain operators) and the well-known 1.1.1.1 public resolver (consumer/private browsing use cases). Cloudflare’s DNS documentation positions the service as resilient and DDoS-protective, and the company’s Anycast DNS explainers emphasize Anycast routing as a mechanism for lower latency and improved availability (and partial protection against DNS flood attacks).

For application security, Cloudflare’s Web Application Firewall (WAF) filters web and API requests using “rulesets,” combining automatic protections with configurable custom rules and rate limiting. Its DDoS protection documentation states that Cloudflare provides “unmetered and unlimited” DDoS protection across layers 3, 4, and 7 for all customers, with managed rulesets that cover L3/4 and L7 attack patterns and continuous updates.

On the developer platform side, Cloudflare Workers is a serverless runtime deployed across Cloudflare’s global network. The Workers runtime is based on Google’s V8 engine and implements browser-like APIs; Cloudflare emphasizes an edge-first model rather than region-by-region deployment. For data, R2 is Cloudflare’s object storage foundation (S3-compatible in positioning), and Cloudflare has built adjacent products atop it—e.g., Cache Reserve is explicitly “built on R2” and designed to increase cache hit ratios while reducing unnecessary egress fees.

Enterprise network and Zero Trust offerings are consolidated under Cloudflare One / Zero Trust. Cloudflare’s docs describe Zero Trust as authenticating and authorizing every request based on identity and context (rather than trusting anything “inside the network”), and positioning the platform as an alternative to “patchwork” appliances and point products. The WARP client is the endpoint component that can route device traffic through Cloudflare for Secure Web Gateway and policy enforcement; Cloudflare documents multiple WARP modes that determine which traffic is sent to Cloudflare and what Zero Trust features are enabled.

At the network edge, Cloudflare’s “Magic Transit” is an enterprise-only network security/performance product that provides DDoS mitigation and traffic acceleration for on-prem, cloud, and hybrid networks. The Magic Transit docs highlight “Bring Your Own IP (BYOIP)” and IP advertisement through Cloudflare locations, while Cloudflare’s reference architecture for Magic Transit emphasizes Anycast tunnel endpoints—one configured tunnel can effectively connect to all Cloudflare global data centers (excluding China) because the Anycast endpoint is announced globally. Cloudflare Load Balancing is positioned as a global “always-on” load balancer with steering options such as Geo steering (country/region/data-center targeting).

### **Edge network architecture**

Cloudflare’s disclosed network footprint (mid-2025) was “equipment at co-location facilities located in more than 330 cities and over 125 countries worldwide,” with continued expansion expected. In parallel, Cloudflare’s resilience whitepaper describes the operational goal as edge reachability and redundancy through Anycast and BGP: data centers are described as locally autonomous and interchangeable through Anycast, with BGP routing traffic to the closest available data center and failing over automatically if a location becomes unavailable. Cloudflare’s Learning Center definitions of Anycast also align: Anycast routing sends requests to one of multiple “nodes,” typically the nearest data center with capacity.

A key architectural differentiator (and also a source of systemic risk) is Cloudflare’s explicit separation of control plane and data plane. In Cloudflare’s resilience whitepaper, the control plane is the “management interface” and “source of truth” for configurations, and it “does not process traffic.” It is described as deployed in a more centralized topology across multiple data centers in a primary region with replication to a secondary region, with ongoing investment toward active-active use. Conversely, the data plane processes customer traffic and is described as not depending on the control plane to operate, because policies are maintained via a globally distributed key-value store (“Quicksilver”) so edge services can continue with a “known good configuration” if connectivity to the control plane is disrupted.

Cloudflare further claims resilience from operational uniformity: “Every Cloudflare service runs in every location” and Cloudflare “run[s] almost every service on every machine,” enabling a data center to be taken offline without customer impact (in the common case) because other locations can take over. This “run everything everywhere” philosophy is central to Cloudflare’s edge platform story (particularly for security and DDoS) because it reduces dependency on a small subset of specialized scrubbing sites.

### **Key technical differentiators**

Cloudflare’s strongest technical differentiators cluster around three themes: Anycast + ubiquity, integrated single-vendor breadth, and rapid deployment of software-defined capabilities at the edge.

Anycast ubiquity enables “load spreading” and fast failover, which is especially valuable for DDoS mitigation and high-availability security enforcement. Cloudflare’s own resilience material emphasizes that IP space is “advertised everywhere” and that Anycast + BGP means traffic routes around outages without requiring clients to switch to a different IP. The company also operates substantial automation to update and maintain its network, which is framed as improving reliability but acknowledged as potentially increasing correlated-failure blast radius if automation fails.

Breadth + integration is also a differentiator in enterprise deals. Cloudflare’s filings describe product suites spanning website/application services, Zero Trust/SASE, developer platform, and consumer offerings. In the Q4’25 earnings call, Cloudflare leadership described a large AI customer choosing Cloudflare over “major hyperscalers” for a unified stack, rapid innovation, and “strategic neutrality,” signing a two-year $85M “pool of funds” deal with 100% traffic allocation. This highlights a technical-commercial coupling: integration breadth makes a “platform” sale feasible, and platform sales deepen switching costs.

Developer platform execution environment design is a third differentiator. Workers uses V8 and a browser-like runtime model at the edge. For AI workloads, Workers AI is explicitly positioned as serverless, GPU-backed inference on Cloudflare’s global network, exposed via APIs rather than region-locked services. Cloudflare has continued to invest in inference performance and operational features (batch inference, speculative decoding, prefix caching, dashboard updates) to make edge inference viable for higher-volume workloads. In parallel, Cloudflare’s 2025 10‑Q notes planned investments in servers with GPUs to support AI-related developer platform products—evidence that the AI roadmap is tied to capital allocation and edge hardware strategy.

### **Patents, intellectual property posture, and open-source contributions**

Cloudflare’s filings state it relies on a combination of patents, copyrights, trademarks, trade secrets, and contractual/confidentiality protections to defend its technology and IP. A review of publicly visible patents assigned to Cloudflare and related acquired entities indicates coverage in areas aligned with its products, including edge security enforcement, proxying, and session/transport innovations. Examples include patents on cross-site request forgery protection at an edge server (US 10,050,792), QUIC and Anycast proxy resiliency mechanisms (e.g., US 12,149,596), and remote browser / isolation-related inventions (e.g., US 10,452,868). These patents can support defensibility, but in infrastructure markets, operational scale, integration, and execution speed often matter more than exclusionary IP rights.

On open source, Cloudflare is unusually active and publishes core infrastructure components. Notable examples include:  
• quiche, Cloudflare’s QUIC + HTTP/3 implementation in Rust   
• Pingora, a Rust-based proxy framework Cloudflare built to replace prior proxy infrastructure at its scale, later open-sourced   
• cloudflared, the open-source client for Cloudflare Tunnel   
• tokio-quiche, an async QUIC library built on quiche and tokio that Cloudflare open-sourced and notes is relied upon in services such as iCloud Private Relay

This open-source posture is strategically relevant: it builds credibility with developers, accelerates ecosystem adoption, and reduces “black box” concerns for enterprise buyers evaluating critical-path infrastructure.

### **Recent product launches and major updates**

Cloudflare runs frequent “innovation weeks” and ships meaningful platform updates. Several recent launches are especially relevant to Cloudflare’s AI-driven strategy:

Cloudflare Data Platform (2025): Cloudflare announced a managed analytics/data platform built on Apache Iceberg and R2, motivated by vendor lock-in and egress fees. Cloudflare explicitly frames R2’s “zero-cost egress” as enabling customers to avoid being locked into one query engine or cloud. Adjacent components include R2 Data Catalog (managed Iceberg catalog) and R2 SQL (serverless query engine), with Cloudflare publishing deep dives on R2 SQL’s distributed execution model.

AI and agents: Cloudflare continued to expand Workers AI performance and capabilities (including faster inference via speculative decoding/prefix caching and batch workload support) and described internal architecture for running more models on fewer GPUs (“Omni”) with routing and configuration tied to Workers KV. Cloudflare also launched “Cloudflare Agents” as persistent, stateful execution environments for agentic workloads powered by Durable Objects, adding primitives like scheduling, real-time communication, and model calls in an edge-native pattern.

AI application security: Cloudflare launched/opened beta for “Firewall for AI,” positioned to discover and protect LLM-powered apps with an initial focus on discovery and PII detection. In parallel, Cloudflare introduced “AI Labyrinth,” an opt-in mitigation that serves AI-generated decoy content to waste the resources of misbehaving AI crawlers and bots that ignore “no crawl” directives.

Post-quantum cryptography: Cloudflare has pushed post-quantum (PQ) cryptography into Zero Trust tunneling workflows, aiming to protect corporate network traffic without forcing every internal app to be upgraded. It also upgraded the WARP client to support post-quantum cryptography to mitigate “harvest-now/decrypt-later” threats.

AI crawlers and content permissioning: Cloudflare expanded from “single-click block AI crawlers” (introduced Sept 2024) to a permission-based model requiring AI companies to obtain explicit permission before scraping, with Cloudflare reporting that >1 million customers had enabled its earlier AI-bot-blocking option.

Mermaid diagram: product-to-architecture relationships (simplified)

```
mermaid
```

**Copy**

```
flowchart LR
  subgraph ControlPlane["Control plane (config + management)"]
    CP[Dashboards, APIs, policy authoring]
    KV[Global config distribution (e.g., distributed KV)]
  end

  subgraph EdgeDataPlane["Data plane (traffic processing at the edge)"]
    EDGE[Edge PoPs / data centers (Anycast)]
    PROXY[Proxy + cache + routing]
    SEC[Security enforcement (WAF, DDoS, bot, rate limit)]
    ZT[Zero Trust enforcement (Gateway/Access policies)]
    LB[Load balancing decisions]
    WORKERS[Workers runtime (edge compute)]
    DATA[R2 + data services]
  end

  CP --> KV --> EDGE
  EDGE --> PROXY
  PROXY --> SEC
  PROXY --> LB
  PROXY --> WORKERS
  WORKERS --> DATA
  ZT --> EDGE
  SEC --> EDGE
```

The control-plane/data-plane separation and “edge everywhere” posture described above are central to why Cloudflare can layer security, performance, and compute functions on the same edge network footprint.

## **Moat and defensibility**

Cloudflare’s defensibility is multi-factor: network scale effects, integrated-product compounding, developer ecosystem dynamics, and commercial switching costs.

Network scale as a security + performance moat: Cloudflare’s presence across hundreds of cities in 125+ countries (as of mid-2025) provides low-latency proximity and the capacity distribution needed for large-scale DDoS mitigation. Anycast routing spreads traffic (including attack traffic) and enables automatic failover, and Cloudflare emphasizes that Anycast is central to redundancy and resilience. This creates a feedback loop: more customers → more traffic → more operational data and routing insight → better performance/security (though Cloudflare does not always quantify the magnitude of these data advantages publicly).

Platform compounding and “breadth-based switching costs”: Cloudflare’s 10-K describes suites spanning website/application services, SASE/Zero Trust, and developer services. As customers adopt multiple layers—DNS, WAF/rulesets, DDoS policies, tunnels, edge compute, storage—switching becomes not just a “CDN swap,” but a re-architecture event. This is reinforced commercially by Cloudflare’s “pool of funds” enterprise contract model, where some large customers commit to spend a minimum over a subscription period but can flex usage across products. Flexibility can accelerate adoption of new products inside a committed commercial envelope, deepening lock-in over time.

Developer ecosystem and distribution: Cloudflare benefits from a large bottom-of-funnel and self-serve motion supported by free and low-cost tiers; its 10-K highlights “millions of Internet properties” using Cloudflare and the strategic importance of free users for awareness and growth. The Q4’25 call noted that paying-customer growth was driven partly by customers graduating from free to small paid accounts, “particularly for our developer platform product.” Open-source contributions (Pingora, quiche, cloudflared) also function as ecosystem multipliers, encouraging experimentation and integrations that funnel usage back to the platform.

Sustainability concerns and vulnerabilities:  
Cloudflare’s core architectural strength—highly shared global systems—creates correlated-failure risk. Cloudflare’s resilience materials explicitly acknowledge the need to isolate failures and invest in change management and chaos testing, reinforcing that operational excellence is a first-order requirement at its scale. Public reporting on major outages in late 2025 underscores the “single provider blast radius” concern: a WAF-related coding error incident reportedly impacted a significant portion of Cloudflare-served HTTP traffic, with broad downstream customer impact.

Geopolitical/regulatory constraints are another vulnerability. Cloudflare’s 10‑Q warns that its China network presence depends on a commercial relationship with JD Cloud and that termination or adverse changes could jeopardize Cloudflare’s ability to offer an integrated global network including China. Finally, competitive pressure is structural: hyperscalers can bundle CDN/WAF/DDoS with compute/storage, while specialized security vendors can compete aggressively in Zero Trust/SSE segments. Cloudflare’s defensibility is therefore most sustainable where it can demonstrably reduce complexity/cost while improving security and performance for multi-cloud and Internet-facing workloads.

## **Customers and use cases**

### **Customer segments and adoption patterns**

Cloudflare’s business spans a wide range—from individual developers and small businesses to the largest enterprises—supported by both self-serve (typically monthly) and contracted enterprise agreements (often 1–3 years). This “land broad, expand upmarket” pattern is visible in disclosed metrics: in Q4’25, Cloudflare reported ~332,000 paying customers (record net adds), with growth driven partly by free-to-paid upgrades. Meanwhile, enterprise scaling is reflected in large-customer concentration: 4,298 customers paying >$100k annually (23% YoY growth) generated 73% of Q4’25 revenue, and Cloudflare ended FY2025 with 269 customers spending >$1M (55% YoY increase).

Cloudflare also reports channel partner contribution: FY2024 revenue was 20% via channel partners and 80% via direct customers (by billing address basis and contracting). This mix suggests Cloudflare is building enterprise distribution beyond pure self-serve while keeping a large direct customer base.

### **Representative case studies and named customers**

The table below lists a set of publicly named customers drawn from Cloudflare-published case studies (not an exhaustive customer list).

**CustomerVerticalHighlighted use caseCloudflare products explicitly referenced**Bank of CyprusBankingAutomatic mitigation of high-volume DDoS attacks without manual intervention or service interruptionMagic Transit Q2Fintech / digital bankingSupport growth with network protection and availability for a major banking platformMagic Transit PayNetFinancial services / paymentsEnsure availability; protect against DDoS while accelerating trafficMagic Transit, WAF OneTrustSaaS (privacy/compliance)Serverless architecture and context-aware Zero Trust accessWorkers, Zero Trust/access management DrataSaaS (security/compliance automation)Adopt integrated Zero Trust tooling and security; integrate SSL automationZero Trust, SSL for SaaS (API referenced) ZendeskSaaS (CX platform)Engineering-led adoption of platform for security/performancePlatform use referenced in Cloudflare case study hub Cloudflare (internal)Technology / security“Dogfooding” Zero Trust to secure workforce and improve productivityCloudflare One DataweaversIT / servicesUse Workers to push apps to global network and improve service speedWorkers

In addition to named case studies, Cloudflare’s earnings call described multiple large AI-related wins (including a two-year $85M “pool of funds” deal and another AI company purchasing Workers + application services), which signals a growing AI-infrastructure customer segment even when customer names are not disclosed.

### **Pricing models and contract structures**

Cloudflare’s filings describe two primary commercial patterns:

Contracted (enterprise/mid-market) subscriptions: Many “contracted customers” sign subscription and support term contracts that “typically range from one to three years,” generally non-cancelable except for cause (failure to perform). Revenue is typically recognized ratably over the subscription term.

Self-serve / pay-as-you-go (often monthly): For customers buying Pro/Business via the website (Cloudflare notes these were previously referred to as self-serve customers), subscription terms are “typically monthly.”

A structurally important enterprise construct is “pool of funds” arrangements for some of Cloudflare’s largest customers, where the customer commits to spend at least a specified amount during the subscription period but is not required to allocate specific amounts to specific products in specific months/quarters. Cloudflare discloses that these arrangements can reduce predictability of timing and revenue recognition compared to more traditional product-specific subscriptions. From a customer perspective, “pool of funds” can function like a committed spend bucket spanning performance, security, and developer platform services, reducing internal procurement friction for adopting new Cloudflare products.

## **Competitive landscape**

Cloudflare operates in multiple overlapping markets—CDN/edge delivery, WAAP/WAF + DDoS, edge compute/serverless, Zero Trust/SASE/SSE, and network services. Competitors differ by “center of gravity”:

Akamai: historically dominant in CDN and enterprise edge/security, offering Prolexic DDoS, Edge DNS, and edge compute (EdgeWorkers).   
Fastly: strong in developer-centric CDN plus programmable edge via WebAssembly-based Compute (formerly Compute@Edge), coupled with Next-Gen WAF.   
Hyperscalers (AWS/GCP/Azure): offer CDNs tightly integrated with their clouds, bundling WAF and DDoS protections and leveraging their own global networks.   
Security specialists (Imperva, Zscaler): compete on WAAP/DDoS (Imperva) and on SSE/Zero Trust access and secure web gateway (Zscaler).

### **Feature comparison table**

This is a directional comparison of major capabilities most relevant to Cloudflare’s core suite. “Yes” indicates the vendor offers a generally comparable product category; “Partial” indicates meaningful coverage but different scope/architecture or reliance on adjacent products/tiers.

**CapabilityCloudflareAkamaiFastlyAWSGoogle CloudMicrosoft AzureImpervaZscaler**CDN / edge cachingYes Yes Yes (Full-Site Delivery) Yes (CloudFront) Yes (Cloud CDN) Yes (Front Door CDN) Partial (CDN exists in portfolio, but WAAP-first positioning) NoAuthoritative DNSYes Yes (Edge DNS) Partial / limitedYes (Route 53; ecosystem) Yes (Cloud DNS; ecosystem)Yes (Azure DNS; ecosystem)PartialNoWAF / WAAPYes Yes (App & API Protector) Yes (Next‑Gen WAF) Yes (AWS WAF) Yes (Cloud Armor WAF rules) Yes (Front Door WAF) Yes Partial (focus is SSE/SWG/ZTNA, not classic WAAP) DDoS mitigation (L3–L7)Yes (unmetered L3/4/7) Yes (Prolexic) Yes (DDoS product referenced) Yes (Shield) Yes (via Cloud Armor + LB) Yes (Front Door includes L3/4/7 + WAF) Yes Partial (SSE focus; DDoS not core)Edge compute / serverlessYes (Workers) Yes (EdgeWorkers) Yes (Compute) Yes (Lambda@Edge) Partial (edge via LB/CDN integrations; compute via cloud services) Partial (functions + edge services; not identical model)NoNoObject storageYes (R2; zero egress positioning) PartialNoYes (S3)Yes (Cloud Storage)Yes (Blob Storage)PartialNoGlobal load balancingYes Yes (GTM listed) PartialYes (ALB/Global Accelerator etc. ecosystem)Yes (Cloud Load Balancing) Yes (Front Door routing) PartialNoZero Trust / ZTNA + SWGYes (Cloudflare One) Partial (enterprise access products exist) LimitedPartial (IAM + network controls; not unified SSE)PartialPartialLimitedYes (Zero Trust Exchange; ZIA/ZPA) Network-layer protection for customer IP space (BGP/Anycast “network edge”)Yes (Magic Transit) Yes (Prolexic BGP/GRE models) LimitedPartialPartialPartialYes (network DDoS) No

Interpretation: Cloudflare’s differentiation is not that each individual feature is unique, but that Cloudflare offers a broad set of performance + security + developer primitives on a single edge footprint with consistent policy and telemetry surfaces. Competitors may match Cloudflare in individual categories (Akamai in DDoS/CDN, Fastly in programmability/CDN, hyperscalers in bundled stacks, Zscaler in SSE), but few match breadth across *all* of: CDN/DNS/WAF/DDoS + edge compute + object storage + Zero Trust + network-layer routing.

## **Financials, growth, and valuation**

### **Revenue growth and profitability profile**

Cloudflare’s revenue trajectory has remained high-growth and has re-accelerated into FY2025. In FY2024, revenue was $1.670B (+29% YoY), and FY2025 revenue was $2.168B (+30% YoY). Q4’25 revenue was $614.5M (+34% YoY).

Margins have compressed somewhat as Cloudflare invests and as paid traffic mix and network expense allocation shifts. In Q4’25 Cloudflare reported non-GAAP gross margin of 74.9% (below its stated long-term target range of 75–77%) and explained that paid-versus-free traffic increased network expense allocation into cost of goods sold. For FY2025, Cloudflare reported GAAP gross margin 74.5% and non-GAAP gross margin 75.8%.

Free cash flow improved meaningfully: Q4’25 free cash flow was $99.4M (16.2% margin), and FY2025 free cash flow was $260.6M (12.0% margin), up from $166.9M (10.0% margin) in FY2024.

### **Customer cohorts, retention, and forward indicators**

Cloudflare discloses “large customers” (>$100k annualized revenue) and dollar-based net retention as key operating metrics. The FY2024 10‑K defines “Annualized Revenue” for this metric as quarterly revenue per customer × 4 (with exclusions) and reports large-customer counts of 2,042 (FY2022), 2,756 (FY2023), and 3,497 (FY2024). In Q4’25, Cloudflare reported 4,298 customers paying >$100k annually.

Dollar-based net retention can be volatile quarter to quarter but is a strong signal of expansion within the base. Cloudflare reported dollar-based net retention of 120% in Q4’25 (up 1% QoQ and 9% YoY). For context, its FY2024 10‑K reported dollar-based net retention of 111% (Q4’24), 115% (Q4’23), and 122% (Q4’22), indicating a trough in 2024 followed by re-acceleration in 2025.

Forward revenue visibility is reflected in RPO. At the end of Q4’25, Cloudflare reported RPO of $2.496B (+48% YoY) and current RPO (revenue expected within 12 months) of 63% of total RPO (+34% YoY). While RPO is not the same as ARR, it is a strong indicator of contracted demand and is particularly meaningful for a business with multi-year subscriptions and “pool of funds” enterprise constructs.

### **Guidance and near-term trend**

For Q1’26, Cloudflare guided to revenue of $620–$621M, and for FY2026, revenue of $2.785–$2.795B (28–29% YoY growth), alongside non-GAAP operating income guidance of $378–$382M for FY2026. This implies continued high growth but a deceleration versus FY2025 as 2025’s re-acceleration normalizes and as scale increases.

### **Valuation snapshot**

As of Feb 23, 2026, Cloudflare’s stock traded around $160.19. Using Cloudflare’s FY2026 weighted-average share count assumption of ~377M shares (from FY2026 EPS guidance), an approximate market capitalization would be ~$60B (illustrative, not exact). Against FY2025 revenue of ~$2.168B, this implies a rough price-to-sales multiple in the high-20s.

Enterprise value (EV) depends on net cash. Cloudflare ended Q4’25 with $4.1B in cash, cash equivalents, and available-for-sale securities. Its latest detailed debt disclosure in the June 30, 2025 10‑Q shows principal amounts of $2.0B (2030 notes) and $1.29375B (2026 notes). Taken together, Cloudflare appears near net-cash-neutral to modest net-cash-positive depending on timing and classification—so EV/Sales is likely similar to P/S on a rough basis (again: approximate, because exact diluted share count and debt balances can change by quarter).

## **Impact from AI and strategic outlook**

### **How AI is shaping Cloudflare’s roadmap**

Cloudflare’s leadership is explicitly framing the “agentic Internet” as a structural shift that multiplies traffic and requires security/performance/compute infrastructure optimized for non-human “users.” In the Q4’25 earnings call, Cloudflare described agents as “infrastructure multipliers” and argued that Cloudflare is positioned to “capture value on both sides” of agentic interactions: (1) AI apps built on Workers, and (2) increased usage across Cloudflare’s broader performance/security/networking products. Management also claimed that weekly requests generated by AI agents “more than doubled” in January 2026 and noted “more than 4.5 million human developers” active on the platform (as described in the same prepared remarks).

Cloudflare’s capital planning reflects this: the June 30, 2025 10‑Q states Cloudflare intends to invest in servers “with graphics processing units (GPUs) to support our AI-related developer platform products.” This links AI opportunity directly to edge hardware (and therefore depreciation, margins, and CapEx efficiency).

### **AI-driven product opportunities**

Edge inference and low-latency AI workloads: Workers AI is positioned as serverless GPU inference on Cloudflare’s global network, exposed via API so developers can run “well-known AI models” without provisioning GPU infrastructure. Cloudflare’s 2025 platform updates emphasize speed/efficiency improvements (speculative decoding, prefix caching) and “batch inference” for high-volume workloads—features that matter for production inference economics. Cloudflare also published a technical deep dive on “Omni,” describing routing of inference requests to the closest instance with available capacity and using Workers KV for model configuration, which is consistent with Cloudflare’s global control-plane/data-plane patterns.

AI app observability and safety: While not exhaustively covered in this report, Cloudflare’s product direction (AI Gateway improvements, log handling, integrations) suggests positioning as a control point for AI traffic: rate limiting, caching, observability, and policy enforcement.

AI security: Cloudflare’s “Firewall for AI” aims to discover and protect LLM-powered apps, initially focused on discovery and PII detection. This extends Cloudflare’s historical advantage in traffic inspection and rulesets into AI-specific threat models (prompt injection, data leakage, malicious tool use). If Cloudflare can standardize these controls (and integrate them into WAF/Zero Trust workflows), it could become a default security layer for public AI applications.

Data platform and AI data gravity: The Cloudflare Data Platform (Iceberg + R2 + querying) is strategically adjacent to AI, because AI workloads increasingly rely on analytics and event pipelines, and because data egress fees shape where data “lives.” Cloudflare explicitly argues that R2’s zero egress makes it possible to avoid being locked into one cloud or query engine. This is a direct attack on “data gravity” lock-in economics used by hyperscalers, potentially strengthening Cloudflare’s neutrality narrative.

### **AI-era risks**

Commoditization and hyperscaler fast-follow: Hyperscalers can bundle CDN/WAF/DDoS/edge compute with AI model platforms and storage. The risk is that buyers accept “good enough” integrated hyperscaler tooling, especially when AI apps are built and hosted primarily inside a single cloud. Evidence from Cloudflare’s earnings call suggests Cloudflare is counter-positioning as “strategically neutral” infrastructure for AI companies that do not want to be dependent on a hyperscaler competitor. Whether this advantage persists depends on how much AI companies value neutrality versus vertical integration and whether Cloudflare can maintain cost/performance parity.

Platform abuse and adversarial actors: AI also increases bot and crawler activity, creating both operational load and a governance problem for content owners. Cloudflare is responding with default blocking/permissioning for AI crawlers and with defensive measures like AI Labyrinth. These controls may deepen Cloudflare’s strategic role as a “gatekeeper” for web content access, but also introduce reputational and policy complexity (Cloudflare becomes an arbiter of which bots are “legitimate,” and disputes with major AI firms could escalate).

Margin and CapEx pressure: AI inference at scale is GPU-intensive. Cloudflare’s plan to deploy GPUs at the edge can pressure gross margins through higher depreciation and power costs, and may increase technological obsolescence risk. Cloudflare’s disclosed focus on efficiency (e.g., running more models on fewer GPUs) is an explicit attempt to defend unit economics.

### **Strategic moves and ecosystem partnerships**

Cloudflare’s AI strategy includes ecosystem partnerships and integrations that make the developer platform “stickier.” Examples include Cloudflare expanding Workers AI with partner models (e.g., Leonardo.Ai for image generation and Deepgram for TTS/STT) and enabling developers to build low-latency AI apps hosted on Cloudflare’s network. Cloudflare has also invested in developer ecosystem integrations such as LangChain support for Workers AI, Vectorize, and D1, lowering adoption friction for teams building with popular AI frameworks.

Finally, Cloudflare’s content-permissioning posture (default blocking, permission requirement, early “Pay Per Crawl” concept) represents a strategic effort to reshape how AI companies access training/inference/search data—potentially turning Cloudflare into a neutral enforcement layer for content licensing in the AI era.
