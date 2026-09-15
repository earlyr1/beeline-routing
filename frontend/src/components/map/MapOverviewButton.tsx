export function MapOverviewButton({ onClick }: { onClick: () => void }) {
  return (
    <button type="button" className="btn btn-small map-overview-button" onClick={onClick}>
      Весь план
    </button>
  );
}
