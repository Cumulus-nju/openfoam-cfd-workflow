"""
Phase 2: Train first AI surrogate model for urban wind prediction.

Task: Given a layout image (buildings + bike locations), predict wind speed
at each candidate bike location using a simple CNN.

This is the minimal viable prototype - train a U-Net style model to predict
the 2D wind speed field at pedestrian height (1.5m) from the geometry layout.

The CFD results serve as "gold standard" training data.
"""

import numpy as np
import os
import sys

# ── Configuration ────────────────────────────────────────────
DATA_DIR = "D:/Phase2_CFD_ML/training_data"
MODEL_DIR = "D:/Phase2_CFD_ML/model_outputs"
CFD_RESULTS_DIR = "D:/Phase2_CFD_ML/cfd_cases/toy_case"

# Spatial parameters matching the toy case
DOMAIN_LX = 200.0  # m
DOMAIN_LY = 250.0  # m
DOMAIN_LZ = 60.0   # m
PEDESTRIAN_HEIGHT_Z = 1.5  # m

# Grid for image representation
GRID_NX = 64  # pixels in x
GRID_NY = 80  # pixels in y

# Wind parameters
WIND_SPEED_REF = 6.0
WIND_DIRECTION_DEG = 180  # from north (negative y), clockwise from x axis

# Buildings and bikes (same as generate_toy_case.py)
BUILDINGS = [
    ("building1", 35.0, 40.0, 20.0, 15.0, 30.0),
    ("building2", 65.0, 65.0, 15.0, 10.0, 12.0),
]

BIKES = [
    ("bike1", 25.0, 75.0, 1.7, 0.5, 1.0),
    ("bike2", 38.0, 70.0, 1.7, 0.5, 1.0),
    ("bike3", 55.0, 78.0, 1.7, 0.5, 1.0),
    ("bike4", 25.0, 55.0, 1.7, 0.5, 1.0),
    ("bike5", 75.0, 50.0, 1.7, 0.5, 1.0),
]

# Domain origin in physical space
X0, Y0 = -30.0, -50.0

# ── Helper: convert physical coords to grid indices ──────────

def phys_to_grid(cx, cy):
    """Convert physical coordinates to grid indices."""
    gx = int((cx - X0) / DOMAIN_LX * GRID_NX)
    gy = int((cy - Y0) / DOMAIN_LY * GRID_NY)
    return max(0, min(GRID_NX - 1, gx)), max(0, min(GRID_NY - 1, gy))

# ── Generate layout image (input channel) ────────────────────

def generate_layout_image():
    """
    Generate a multi-channel "image" of the urban layout.

    Returns:
        np.ndarray: shape (C, H, W) where:
          channel 0: building footprint (1 where building, 0 elsewhere)
          channel 1: building height (normalized)
          channel 2: bike location markers
    """
    layout = np.zeros((3, GRID_NY, GRID_NX), dtype=np.float32)
    max_height = max(h for _, _, _, _, _, h in BUILDINGS)

    # Channel 0/1: Buildings
    for name, cx, cy, lx, ly, h in BUILDINGS:
        x0, y0 = phys_to_grid(cx - lx/2, cy - ly/2)
        x1, y1 = phys_to_grid(cx + lx/2, cy + ly/2)
        x0, x1 = max(0, min(x0, x1)), min(GRID_NX - 1, max(x0, x1))
        y0, y1 = max(0, min(y0, y1)), min(GRID_NY - 1, max(y0, y1))
        layout[0, y0:y1+1, x0:x1+1] = 1.0
        layout[1, y0:y1+1, x0:x1+1] = h / max_height

    # Channel 2: Bike markers
    for name, cx, cy, lx, ly, h in BIKES:
        bx, by = phys_to_grid(cx, cy)
        # Mark a 2x2 region around the bike center
        r = 1
        for dy in range(-r, r+1):
            for dx in range(-r, r+1):
                px, py = bx + dx, by + dy
                if 0 <= px < GRID_NX and 0 <= py < GRID_NY:
                    layout[2, py, px] = 1.0

    return layout


# ── Synthetic wind field (for initial testing before CFD data) ──

def generate_synthetic_wind_field(layout):
    """
    Generate a simple synthetic wind field for prototyping.
    This is used for testing the ML pipeline BEFORE real CFD data is available.

    Uses simplified potential flow around buildings:
    - Background flow: uniform from north (U0=0, V0=-1)
    - Building effects: wake deficit, corner acceleration

    Args:
        layout: (C, H, W) layout image

    Returns:
        u, v: velocity components at each grid cell
    """
    ny, nx = GRID_NY, GRID_NX
    bldg_mask = layout[0]  # building footprints

    # Background flow (from north = negative y direction)
    u_bg = np.zeros((ny, nx))  # x-component: 0
    v_bg = -np.ones((ny, nx)) * WIND_SPEED_REF / 10.0  # scaled for visualization

    # Find building centers
    from scipy.ndimage import label, center_of_mass
    labeled, n_bldgs = label(bldg_mask)

    u = u_bg.copy()
    v = v_bg.copy()

    for i in range(1, n_bldgs + 1):
        mask = labeled == i
        cy, cx = center_of_mass(mask)

        # Create a coordinate grid relative to building center
        yy, xx = np.ogrid[:ny, :nx]
        dy = yy - cy
        dx = xx - cx

        dist = np.sqrt(dx**2 + dy**2)

        # Wake region (downstream = positive y from building = south side)
        # Wind from north, so wake is south of building
        wake_region = (dy > 0) & (dy < 40) & (np.abs(dx) < 15)
        wake_strength = np.exp(-np.abs(dx) / 10.0) * np.exp(-dy / 30.0)
        wake_strength *= 0.4  # 40% speed reduction max

        v[wake_region] += wake_strength[wake_region] * np.abs(v_bg[wake_region])

        # Corner acceleration (upstream corners)
        corner_region = (dy > -10) & (dy < 10) & (np.abs(dx) > 5) & (np.abs(dx) < 20)
        corner_strength = 0.3
        v[corner_region] += corner_strength * np.abs(v_bg[corner_region])

        # Set velocity to 0 inside buildings
        u[mask] = 0
        v[mask] = 0

    return u, v


# ── Dataset generation ──────────────────────────────────────

def create_dataset(num_samples=1):
    """
    Create the training dataset.

    For the toy prototype (single case), we generate pairs of:
      (layout_image, wind_field)

    For the real Phase 2, this would load multiple CFD simulation results.

    Args:
        num_samples: number of layout variations to generate

    Returns:
        X_train: layout images (N, C, H, W)
        Y_train: wind fields (N, 2, H, W) - (u, v) components
    """
    X_list = []
    Y_list = []

    for i in range(num_samples):
        layout = generate_layout_image()
        u, v = generate_synthetic_wind_field(layout)
        wind = np.stack([u, v], axis=0)

        X_list.append(layout)
        Y_list.append(wind)

    return np.stack(X_list), np.stack(Y_list)


# ── Simple CNN model ─────────────────────────────────────────

def build_unet_model(input_channels=3, output_channels=2):
    """
    Build a minimal U-Net for wind field prediction.
    """
    import torch
    import torch.nn as nn

    class MiniUNet(nn.Module):
        def __init__(self):
            super().__init__()
            # Encoder
            self.enc1 = nn.Sequential(
                nn.Conv2d(input_channels, 16, 3, padding=1),
                nn.ReLU(),
                nn.Conv2d(16, 16, 3, padding=1),
                nn.ReLU(),
            )
            self.pool1 = nn.MaxPool2d(2)  # -> H/2, W/2

            self.enc2 = nn.Sequential(
                nn.Conv2d(16, 32, 3, padding=1),
                nn.ReLU(),
                nn.Conv2d(32, 32, 3, padding=1),
                nn.ReLU(),
            )
            self.pool2 = nn.MaxPool2d(2)  # -> H/4, W/4

            # Bottleneck
            self.bottleneck = nn.Sequential(
                nn.Conv2d(32, 64, 3, padding=1),
                nn.ReLU(),
                nn.Conv2d(64, 32, 3, padding=1),
                nn.ReLU(),
            )

            # Decoder
            self.up1 = nn.Upsample(scale_factor=2, mode='bilinear', align_corners=False)
            self.dec1 = nn.Sequential(
                nn.Conv2d(32 + 32, 32, 3, padding=1),
                nn.ReLU(),
                nn.Conv2d(32, 16, 3, padding=1),
                nn.ReLU(),
            )

            self.up2 = nn.Upsample(scale_factor=2, mode='bilinear', align_corners=False)
            self.dec2 = nn.Sequential(
                nn.Conv2d(16 + 16, 16, 3, padding=1),
                nn.ReLU(),
                nn.Conv2d(16, output_channels, 3, padding=1),
            )

        def forward(self, x):
            e1 = self.enc1(x)
            e2 = self.enc2(self.pool1(e1))
            b = self.bottleneck(self.pool2(e2))
            d1 = self.dec1(torch.cat([self.up1(b), e2], dim=1))
            d2 = self.dec2(torch.cat([self.up2(d1), e1], dim=1))
            return d2

    return MiniUNet()


# ── Training loop ────────────────────────────────────────────

def train_model(model, X_train, Y_train, epochs=200, lr=1e-3):
    """
    Train the model on the layout-to-wind mapping.
    """
    import torch
    import torch.nn as nn

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Using device: {device}")

    model = model.to(device)
    X = torch.tensor(X_train, dtype=torch.float32).to(device)
    Y = torch.tensor(Y_train, dtype=torch.float32).to(device)

    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    criterion = nn.MSELoss()

    model.train()
    losses = []

    for epoch in range(epochs):
        optimizer.zero_grad()
        pred = model(X)
        loss = criterion(pred, Y)
        loss.backward()
        optimizer.step()

        losses.append(loss.item())
        if epoch % 20 == 0:
            print(f"  Epoch {epoch:4d}/{epochs}  Loss: {loss.item():.6f}")

    print(f"  Final loss: {losses[-1]:.6f}")
    return model, losses


# ── Main ─────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Phase 2: Urban Wind Surrogate Model Training (Prototype)")
    print("=" * 60)

    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(MODEL_DIR, exist_ok=True)

    # Step 1: Create dataset (synthetic for now; replace with CFD data later)
    print("\n[1] Generating synthetic training data...")
    X, Y = create_dataset(num_samples=1)
    print(f"  X shape: {X.shape} (N, C, H, W)")
    print(f"  Y shape: {Y.shape} (N, 2, H, W)")

    # Save layout image for visualization
    np.save(os.path.join(DATA_DIR, "layout.npy"), X[0])
    np.save(os.path.join(DATA_DIR, "wind_field.npy"), Y[0])
    print(f"  Saved to {DATA_DIR}")

    # Step 2: Build model
    print("\n[2] Building Mini-UNet model...")
    model = build_unet_model()
    total_params = sum(p.numel() for p in model.parameters())
    print(f"  Total parameters: {total_params:,}")

    # Step 3: Train
    print("\n[3] Training...")
    model, losses = train_model(model, X, Y, epochs=100)

    # Step 4: Predict and evaluate
    print("\n[4] Running prediction...")
    import torch
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.eval()
    with torch.no_grad():
        X_t = torch.tensor(X, dtype=torch.float32).to(device)
        pred = model(X_t).cpu().numpy()

    # Extract bike location wind speeds
    print("\n[5] Wind speed at bike locations (synthetic data):")
    bike_speeds = []
    for name, cx, cy, lx, ly, h in BIKES:
        bx, by = phys_to_grid(cx, cy)
        u_val = Y[0, 0, by, bx] * 10.0  # rescale back
        v_val = Y[0, 1, by, bx] * 10.0
        speed = np.sqrt(u_val**2 + v_val**2)
        bike_speeds.append((name, cx, cy, u_val, v_val, speed))
        print(f"  {name} at ({cx}, {cy}): U={u_val:.2f}, V={v_val:.2f}, "
              f"|V|={speed:.2f} m/s")

    # Save model
    model_path = os.path.join(MODEL_DIR, "surrogate_model.pt")
    torch.save(model.state_dict(), model_path)
    print(f"\n  Model saved to {model_path}")

    # Save bike speed data
    import json
    bike_data = [
        {"name": n, "cx": cx, "cy": cy, "U": float(u), "V": float(v), "speed": float(s)}
        for n, cx, cy, u, v, s in bike_speeds
    ]
    with open(os.path.join(MODEL_DIR, "bike_wind_speeds.json"), "w") as f:
        json.dump(bike_data, f, indent=2)

    print("\n" + "=" * 60)
    print("Phase 2 prototype training complete!")
    print("Next: replace synthetic data with real CFD results.")
    print("=" * 60)

    return model, losses


if __name__ == "__main__":
    main()
