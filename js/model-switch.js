/*
 * A live, in-browser toggle between reconstruction models -- for a recipe that
 * exports more than one (`geode.globe(reconstruction=[...])`; see `view.json`'s
 * `reconstructions[]`/`initialReconstruction`). A recipe with none, or only one,
 * is untouched: `mountReconstructionSwitcher` is a drop-in replacement for
 * `mountExplorer` that degrades to it exactly, so a page can call this
 * unconditionally without knowing in advance whether its own recipe is
 * multi-model.
 *
 * The swap is a full remount, not a surgical patch of the running layers: this
 * page has no boundaries/velocities series (the expensive layers a targeted
 * rebuild exists to avoid re-fetching), so re-running `mountExplorer` into the
 * same root is the lower-risk choice -- it touches `explorer.js`'s own shared
 * internals (draw loop, legend, hover closures) not at all, only through the
 * two small, additive hooks `mountExplorer` already exposes for this
 * (`opts.legendExtra`, `globe.state`).
 */

import { mountExplorer } from './explorer.js';

function el(tag, className) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
}

/**
 * @param recipe  the view record, possibly carrying `reconstructions[]`
 * @param root    an element to fill; passed straight through to `mountExplorer`
 * @returns       whatever the current `mountExplorer(...)` call returns
 */
export async function mountReconstructionSwitcher(recipe, root) {
  const models = recipe.reconstructions;
  if (!models || models.length < 2) {
    return mountExplorer(recipe, root);
  }

  const select = el('select', 'dtm-model-select');
  select.setAttribute('aria-label', 'Reconstruction model');
  for (const m of models) {
    const opt = el('option');
    opt.value = m.id;
    opt.textContent = m.label ?? m.id;
    select.append(opt);
  }
  select.value = recipe.initialReconstruction ?? models[0].id;

  let handle = await mountExplorer(recipe, root, { legendExtra: select });

  select.addEventListener('change', async () => {
    const next = models.find((m) => m.id === select.value);
    if (!next) return;

    // Carry the live camera/clock forward as VALUES, not as running state --
    // this is a fresh mount, and nothing about the old layers survives it
    // except what these four numbers capture.
    const { lon, lat, zoom, age } = handle.globe.state;

    const swapped = {
      ...recipe,
      camera: { ...recipe.camera, lon, lat, zoom },
      time: { ...recipe.time, initial: age },
      layers: recipe.layers.map((layer) => {
        const url = next.layerUrls[layer.id ?? layer.kind];
        // A layer this model has no export for (should not happen for any layer
        // every model was built from, but a future page might add a model-only
        // layer) is left with its old URL rather than silently vanishing.
        return url ? { ...layer, url } : layer;
      }),
    };

    // `mountExplorer` rebuilds the DOM from scratch (`root.innerHTML = ''`), but
    // `select` is the SAME element on every call, not a fresh one -- re-appending
    // an already-attached node moves it rather than duplicating it, so its value,
    // options and this very listener all survive the remount untouched.
    handle = await mountExplorer(swapped, root, { legendExtra: select });
    select.value = next.id;
  });

  return handle;
}
