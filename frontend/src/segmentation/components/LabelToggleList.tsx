import type { SegmentationLabel } from '@/types/segmentation';

interface LabelToggleListProps {
  labels: SegmentationLabel[];
  enabledLabels: Set<number>;
  onToggle: (labelIndex: number) => void;
}

/**
 * LabelToggleList
 *
 * Displays a list of segmentation labels with color swatches
 * and toggle checkboxes. Controls which label classes are
 * visible in the overlay rendering.
 */
export function LabelToggleList({
  labels,
  enabledLabels,
  onToggle,
}: LabelToggleListProps) {
  if (labels.length === 0) {
    return (
      <div className="text-xs text-muted-foreground">
        No segmentation labels available.
      </div>
    );
  }

  return (
    <div className="space-y-1">
      {labels
        .filter((l) => l.index > 0) // hide background
        .map((label) => {
          const [r, g, b] = label.color;
          const hexColor = `rgb(${r}, ${g}, ${b})`;
          const isChecked = enabledLabels.has(label.index);

          return (
            <label
              key={label.index}
              className="flex cursor-pointer items-center gap-2 rounded px-2 py-1 text-xs hover:bg-accent"
            >
              <input
                type="checkbox"
                checked={isChecked}
                onChange={() => onToggle(label.index)}
                className="h-3.5 w-3.5 accent-primary"
              />
              <span
                className="inline-block h-3 w-3 rounded-sm border border-border"
                style={{ backgroundColor: hexColor }}
              />
              <span className="text-foreground">{label.name}</span>
              <span className="ml-auto text-muted-foreground">
                #{label.index}
              </span>
            </label>
          );
        })}
    </div>
  );
}
