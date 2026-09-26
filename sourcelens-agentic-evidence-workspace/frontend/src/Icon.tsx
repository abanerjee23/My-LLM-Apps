export function Icon({name, className = ""}: {name: string; className?: string}) {
  const paths: Record<string, React.ReactNode> = {
    investigations: <><path d="m21 21-5-5"/><circle cx="10" cy="10" r="7"/><path d="M7 11V9m3 4V7m3 4V9"/></>,
    sources: <><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/></>,
    notebook: <><path d="M5 3h13a1 1 0 0 1 1 1v17H6a3 3 0 0 1-3-3V5a2 2 0 0 1 2-2ZM3 17h16M8 7h7M8 11h5"/></>,
    trend: <><path d="M3 17 9 11l4 4 8-10M15 5h6v6"/></>,
    feedback: <><path d="M20 11a8 8 0 0 1-8 8H4l-2 3V11a9 9 0 0 1 18 0Z"/><path d="M7 10h8M7 14h5"/></>,
    arrow: <><path d="M5 12h14m-6-6 6 6-6 6"/></>,
  };
  return <svg className={`ui-icon ${className}`} width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{paths[name] || paths.sources}</svg>;
}
