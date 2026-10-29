/** A decorative 1200 x 1200 illustration above a section title. Its black edge is blended away in landing.css. */
export function Art({ file }: { file: string }) {
  return (
    // eslint-disable-next-line @next/next/no-img-element -- static export: a plain img with its real size
    <img className="art" src={`/landing/${file}`} width={1200} height={1200} alt="" aria-hidden="true" loading="lazy" decoding="async" />
  );
}
