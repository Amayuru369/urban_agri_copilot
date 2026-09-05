with open("frontend/garden.html", "r", encoding="utf-8", errors="ignore") as f:
    code = f.read()

import re

# 1. Helper to get known locations
validation_helper = """
function getKnownLocations() {
  let list = [];
  try {
    const raw = localStorage.getItem('saved_garden_locations');
    if (raw) list = JSON.parse(raw);
  } catch (_) {}
  return list;
}

function checkLocationNameCollision(name, lat, lon) {
  if (!name) return null;
  const cleanName = name.trim().toLowerCase();
  const known = getKnownLocations();
  const match = known.find(k => k.name.trim().toLowerCase() === cleanName);
  
  if (!match) return null; // Name is completely new -> OK
  
  // If name matches an existing one, check if coords are essentially identical
  const dLat = Math.abs(Number(match.lat) - Number(lat));
  const dLon = Math.abs(Number(match.lon) - Number(lon));
  if (dLat < 0.0001 && dLon < 0.0001) {
    return null; // Same spot -> OK
  }
  
  return match; // Collision: same name, different coordinates!
}
"""

if "function checkLocationNameCollision" not in code:
    code = validation_helper + "\n" + code

# 2. Add validation inside the form submit listener (add-plant-form or plant registration)
# Find the submit handler for the plant form
submit_pattern = r"(document\.getElementById\(['\"]plant-form['\"]|document\.getElementById\(['\"]add-plant-form['\"]\)|form\.addEventListener\(['\"]submit['\"]).*?\{"

# Let's inspect where the submit event is attached for plant registration
hook_code = """
    // Prevent duplicate location name with divergent coordinates
    const _plotSelect = document.getElementById('plot_select');
    const _locName = (document.getElementById('location_name')?.value || '').trim();
    const _lat = parseFloat(document.getElementById('latitude')?.value);
    const _lon = parseFloat(document.getElementById('longitude')?.value);

    if (_plotSelect && _plotSelect.value === 'new' && _locName) {
      const collision = checkLocationNameCollision(_locName, _lat, _lon);
      if (collision) {
        alert(`The location name "${_locName}" is already used for coordinates (${Number(collision.lat).toFixed(4)}, ${Number(collision.lon).toFixed(4)}).\n\nPlease pick a unique name or choose the existing location from the "Saved Location" dropdown.`);
        if (btn) {
          btn.disabled = false;
          btn.textContent = 'Register Plant';
        }
        return;
      }
    }
"""

# Inject right after form submission begins
if "btn.textContent = 'Registering...';" in code:
    code = code.replace(
        "btn.textContent = 'Registering...';",
        "btn.textContent = 'Registering...';\n" + hook_code,
        1
    )
    print("Injected duplicate location collision check into plant registration submit.")
elif "e.preventDefault();" in code:
    # Fallback injection after preventDefault in the registration form
    form_start = code.find("document.getElementById('plant-form')")
    if form_start == -1:
        form_start = code.find("document.getElementById('add-plant-form')")
    if form_start != -1:
        prev_def = code.find("e.preventDefault();", form_start)
        if prev_def != -1:
            code = code[:prev_def + len("e.preventDefault();")] + "\n" + hook_code + code[prev_def + len("e.preventDefault();"):]
            print("Injected duplicate location check after e.preventDefault().")

# 3. Add real-time input alert for location_name field
realtime_check = """
  const locInput = document.getElementById('location_name');
  if (locInput && !locInput.dataset.collisionBound) {
    locInput.addEventListener('input', (e) => {
      const plotSel = document.getElementById('plot_select');
      if (plotSel && plotSel.value !== 'new') return;
      
      const val = e.target.value.trim().toLowerCase();
      if (!val) return;
      
      const known = getKnownLocations();
      const existing = known.find(k => k.name.trim().toLowerCase() === val);
      if (existing) {
        locInput.setCustomValidity(`"${existing.name}" already exists. Use the dropdown to select it or choose a new name.`);
      } else {
        locInput.setCustomValidity('');
      }
    });
    locInput.dataset.collisionBound = "true";
  }
"""

if "function loadSavedGardenPlots" in code:
    code = code.replace(
        "async function loadSavedGardenPlots() {",
        "async function loadSavedGardenPlots() {\n" + realtime_check,
        1
    )

with open("frontend/garden.html", "w", encoding="utf-8") as f:
    f.write(code)

print("Saved frontend/garden.html")
