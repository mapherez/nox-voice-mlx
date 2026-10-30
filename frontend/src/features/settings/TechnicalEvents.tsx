import { useRuntime, useSnapshot } from "../../app/workspace";
export function TechnicalEvents() {
  const { diagnostics } = useRuntime(),
    { events, eventsError } = useSnapshot(diagnostics);
  if (eventsError) return <li>{eventsError}</li>;
  if (events === null) return <li>No events loaded.</li>;
  if (!events.length) return <li>No events yet.</li>;
  return events.map((event, index) => {
    const date = new Date(event.time * 1000);
    return (
      <li key={event.time + ":" + index}>
        <time dateTime={date.toISOString()}>
          {date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}
        </time>
        <span>{event.message}</span>
      </li>
    );
  });
}
