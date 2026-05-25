import { useState } from 'react';
import { Button } from '@components/ui/button';
import { useSimulationStore } from '@store/useSimulationStore';

/**
 * SimulationPage
 *
 * Lesion and organ simulation interface. Allows users to:
 * - Configure synthetic lesion parameters (type, shape, HU values)
 * - Simulate organ tissues with realistic density patterns
 * - Apply deformation fields to existing anatomy
 * - Preview and export simulation results
 */

export default function SimulationPage() {
  const { lesions, organs, addLesion } = useSimulationStore();
  const [activeTab, setActiveTab] = useState<'lesion' | 'organ' | 'deformation'>('lesion');

  return (
    <div className="flex h-full flex-col p-6">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-foreground">Lesion Simulation</h1>
        <p className="text-sm text-muted-foreground">
          Generate synthetic lesions and organs for AI training and validation
        </p>
      </div>

      {/* Tab navigation */}
      <div className="mb-6 flex gap-2 border-b border-border">
        {(['lesion', 'organ', 'deformation'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
              activeTab === tab
                ? 'border-primary text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            {tab.charAt(0).toUpperCase() + tab.slice(1)}
          </button>
        ))}
      </div>

      {/* Configuration panel */}
      <div className="mb-6 grid gap-6 lg:grid-cols-2">
        {/* Lesion parameters */}
        <div className="rounded-lg border border-border bg-card p-4">
          <h3 className="mb-4 text-sm font-medium">Lesion Parameters</h3>
          <div className="space-y-4">
            <div>
              <label className="text-xs text-muted-foreground">Lesion Type</label>
              <select className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm">
                <option value="tumor">Tumor</option>
                <option value="nodule">Nodule</option>
                <option value="cyst">Cyst</option>
                <option value="calcification">Calcification</option>
                <option value="metastasis">Metastasis</option>
              </select>
            </div>
            <div>
              <label className="text-xs text-muted-foreground">Shape</label>
              <select className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm">
                <option value="spherical">Spherical</option>
                <option value="ellipsoidal">Ellipsoidal</option>
                <option value="irregular">Irregular</option>
                <option value="lobulated">Lobulated</option>
                <option value="spiculated">Spiculated</option>
              </select>
            </div>
            <div>
              <label className="text-xs text-muted-foreground">
                Mean HU Value: <span className="text-primary">40</span>
              </label>
              <input
                type="range"
                min="-1000"
                max="1000"
                defaultValue={40}
                className="mt-1 w-full"
              />
            </div>
            <div>
              <label className="text-xs text-muted-foreground">
                Diameter (mm): <span className="text-primary">20</span>
              </label>
              <input
                type="range"
                min="2"
                max="100"
                defaultValue={20}
                className="mt-1 w-full"
              />
            </div>
            <Button onClick={() => addLesion({} as any)} className="w-full">
              Add Lesion
            </Button>
          </div>
        </div>

        {/* Lesion list */}
        <div className="rounded-lg border border-border bg-card p-4">
          <h3 className="mb-4 text-sm font-medium">Lesion List ({lesions.length})</h3>
          {lesions.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No lesions configured. Add lesions using the parameters panel.
            </p>
          ) : (
            <div className="space-y-2">
              {lesions.map((lesion, index) => (
                <div key={index} className="rounded border border-border p-2 text-sm">
                  Lesion {index + 1}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Action buttons */}
      <div className="mt-auto flex gap-2 border-t border-border pt-4">
        <Button variant="default">Run Simulation</Button>
        <Button variant="outline">Preview</Button>
        <Button variant="ghost">Export Results</Button>
      </div>
    </div>
  );
}
