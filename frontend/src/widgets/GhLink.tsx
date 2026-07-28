// The person link used across report tables/lists — an in-app link to the /person
// view (href="#person" + data-person, picked up by the person route / drill handler).
// Ported verbatim from the three byte-identical in-page copies (Flow/People/Traffic).
export default function GhLink({ login }: { login: string }) {
  return (
    <a className="gh" href="#person" data-person={login} title={`Open ${login}'s Person page`}>
      {login}
    </a>
  );
}
