import type { ReactNode } from "react";
import "./workshop-home-frame.css";

/** Optional presentational frame. Product queries, actions and recent content stay product-owned. */
export type WorkshopHomeFrameProps = {
  title: string;
  icon: ReactNode;
  links?: ReactNode;
  headline: ReactNode;
  description: string;
  primaryAction: ReactNode;
  secondaryAction?: ReactNode;
  illustration: { src: string; alt: string };
  mascot: ReactNode;
  children: ReactNode;
  className?: string;
};
export function WorkshopHomeFrame({
  title,
  icon,
  links,
  headline,
  description,
  primaryAction,
  secondaryAction,
  illustration,
  mascot,
  children,
  className = "",
}: WorkshopHomeFrameProps) {
  return (
    <section
      className={`workshop-home-frame ${className}`}
      aria-label={`${title}首页`}
    >
      <header className="workshop-home-frame__heading">
        <h1>
          <span className="workshop-home-frame__mark">{icon}</span>
          {title}
        </h1>
        <nav aria-label="工坊工具">{links}</nav>
      </header>
      <div className="workshop-home-frame__hero">
        <div className="workshop-home-frame__intro">
          <h2>{headline}</h2>
          <span className="workshop-home-frame__stroke" aria-hidden />
          <p>{description}</p>
          <div className="workshop-home-frame__actions">
            {primaryAction}
            {secondaryAction}
          </div>
        </div>
        <div className="workshop-home-frame__scene">
          <img
            src={illustration.src}
            alt={illustration.alt}
            width="1672"
            height="941"
            fetchPriority="high"
          />
          <div className="workshop-home-frame__mascot">{mascot}</div>
        </div>
      </div>
      {children}
    </section>
  );
}
