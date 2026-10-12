import { Notice, PageTitle } from "../components/ui";

/** An honest placeholder for a screen whose functionality belongs to a later implementation phase. */
export function NotYetPage({ title, phase, detail }: { title: string; phase: number; detail: string }) {
  return (
    <div>
      <PageTitle title={title} />
      <Notice title={`Not available yet — planned for Phase ${phase}`}>
        <p>{detail}</p>
      </Notice>
    </div>
  );
}
