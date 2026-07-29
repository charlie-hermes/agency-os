import Link from "next/link";

export default function NotFound() {
  return <main className="error-page"><span className="eyebrow accent">Not found</span><h1>This Fleet view does not exist.</h1><p>Check the address or return to your workspace.</p><Link className="primary-link" href="/">Return to Fleet</Link></main>;
}
