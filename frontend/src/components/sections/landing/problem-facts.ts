// The only numbers on the landing that do not come from a run file. Each one has a source, a year and a link.
export const FACTS = [
  {
    value: "14%",
    text: "of ASIC and SoC projects worked on first silicon in 2024.",
    source: "Wilson Research Group functional verification study, 2024",
    href: "https://verificationacademy.com/topics/planning-measurement-and-analysis/wrg-industry-data-and-trends/2024-siemens-eda-and-wilson-research-group-ic-asic-functional-verification-trend-report/",
  },
  {
    value: "67,000",
    text: "US semiconductor jobs projected to go unfilled by 2030, across technicians, computer scientists and engineers.",
    source: "SIA and Oxford Economics, 2023",
    href: "https://www.semiconductors.org/america-faces-significant-shortage-of-tech-workers-in-semiconductor-industry-and-throughout-u-s-economy/",
  },
  {
    value: "44.2%",
    text: "of GPT-4-Turbo Verilog that passed its RTLLM v2 tests still failed a formal check.",
    source: "RealBench, arXiv 2507.16200, 2025",
    href: "https://arxiv.org/abs/2507.16200",
  },
] as const;
