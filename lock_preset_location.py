with open("frontend/garden.html", "r", encoding="utf-8", errors="ignore") as f:
    code = f.read()

import re

# 1. Update the select listener inside loadSavedGardenPlots to lock/unlock location_name
old_listener = """    // 5. Wire onchange listener once
    if (!select.dataset.bound) {
      select.addEventListener('change', (e) => {
        const nameInput = document.getElementById('location_name');
        if (e.target.value === 'new') {
          if (nameInput) nameInput.value = '';
          return;
        }

        try {
          const loc = JSON.parse(e.target.value);
          if (nameInput) nameInput.value = loc.name;
          if (typeof updateLocation === 'function') {
            updateLocation(loc.lat, loc.lon, true);
          }
        } catch (_) {}
      });
      select.dataset.bound = "true";
    }"""

new_listener = """    // 5. Wire onchange listener: lock location_name when an existing preset is selected
    if (!select.dataset.bound) {
      select.addEventListener('change', (e) => {
        const nameInput = document.getElementById('location_name');
        if (e.target.value === 'new') {
          if (nameInput) {
            nameInput.value = '';
            nameInput.readOnly = false;
            nameInput.classList.remove('bg-gray-100', 'cursor-not-allowed', 'text-gray-500');
          }
          return;
        }

        try {
          const loc = JSON.parse(e.target.value);
          if (nameInput) {
            nameInput.value = loc.name;
            nameInput.readOnly = true;
            nameInput.classList.add('bg-gray-100', 'cursor-not-allowed', 'text-gray-500');
          }
          if (typeof updateLocation === 'function') {
            updateLocation(loc.lat, loc.lon, true);
          }
        } catch (_) {}
      });
      select.dataset.bound = "true";
    }"""

if old_listener in code:
    code = code.replace(old_listener, new_listener)
    print("Updated select change listener to lock/unlock location_name.")
else:
    print("Exact old_listener block not found; checking regex fallback...")
    code = re.sub(
        r"// 5\. Wire onchange listener once[\s\S]*?select\.dataset\.bound = \"true\";\s*\}",
        new_listener.strip(),
        code
    )

# 2. Update updateLocation() so moving the map marker resets plot_select to "new" unless triggered programmatically
sync_reset_hook = """  // Reset dropdown to "new" if user manually moved pin away from saved preset
  if (!skipMapPan) {
    const _plotSelect = document.getElementById('plot_select');
    const _nameInput = document.getElementById('location_name');
    if (_plotSelect && _plotSelect.value !== 'new') {
      _plotSelect.value = 'new';
      if (_nameInput) {
        _nameInput.readOnly = false;
        _nameInput.classList.remove('bg-gray-100', 'cursor-not-allowed', 'text-gray-500');
      }
    }
  }"""

if "function updateLocation(lat, lon, skipMapPan = false) {" in code:
    code = code.replace(
        "function updateLocation(lat, lon, skipMapPan = false) {",
        "function updateLocation(lat, lon, skipMapPan = false) {\n" + sync_reset_hook
    )
    print("Hooked marker movements to safely reset preset dropdown.")

with open("frontend/garden.html", "w", encoding="utf-8") as f:
    f.write(code)

print("Saved frontend/garden.html")
