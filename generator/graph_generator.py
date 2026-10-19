import matplotlib.pyplot as plt
import matplotlib.patches as patches
import squarify
import textwrap

import io
import base64

def filter_entries_for_semester(entries, current_semester=None):
    """
    Returns the entries relevant to a single semester's snapshot: hidden
    (event) entries are always included since the events data doesn't carry
    semester info; non-hidden (capability-earned) entries are included only
    if their semester matches current_semester. Pass current_semester=None
    to return every entry unfiltered.

    Shared by the treemap and the overview page's activity/capability
    counters so the two stay consistent with each other.
    """
    if current_semester is None:
        return list(entries)
    target = current_semester.strip().lower()
    return [
        e for e in entries
        if e.get('hidden') or str(e.get('semester', '')).strip().lower() == target
    ]

def generate_tree_graph(student_data, output_path=None, return_base64=False, current_semester=None):
    """
    Generates a treemap of capabilities for the overview page (Top 3 pages).
    Matches the infographic style with rounded corners, maroon color scheme, and proper text wrapping.
    If return_base64 is True, returns the base64 encoded string of the image.
    Otherwise, saves to output_path and returns the path.

    Counts every entry per capability: both capability-earned write-ups and
    the hidden, lower-touch event entries. If current_semester is provided,
    capability-earned entries are restricted to that semester; hidden event
    entries are always included regardless, since the events data doesn't
    carry semester info yet. Leave current_semester as None to count every
    entry unfiltered.
    """
    capabilities = student_data.get('capabilities', [])

    # 1. Prepare Data
    counts = []

    # Collect all valid entries
    raw_data = [] # List of (count, name)
    for cap in capabilities:
        entries = filter_entries_for_semester(cap.get('entries', []), current_semester)
        c = len(entries)
        if c > 0:
            raw_data.append({'count': c, 'name': cap['name']})
            
    # Sort by count desc (squarify likes sorted data for better aspect ratios usually)
    raw_data.sort(key=lambda x: x['count'], reverse=True)
    
    counts = [item['count'] for item in raw_data]
    # Store data objects to access name/count separately in the loop
    items = raw_data 

    # Handle empty case
    if not counts:
        fig, ax = plt.subplots(figsize=(10, 5))
        ax.text(0.5, 0.5, "No Activities Recorded", ha='center', va='center', 
                fontsize=14, color='#888888', fontfamily='sans-serif')
        ax.axis('off')
        
        if return_base64:
            buf = io.BytesIO()
            plt.savefig(buf, format='png', dpi=300, bbox_inches='tight')
            plt.close()
            buf.seek(0)
            return base64.b64encode(buf.read()).decode('utf-8')
        else:
            plt.savefig(output_path, dpi=300, bbox_inches='tight')
            plt.close()
            return output_path

    # 2. Define Colors
    # Dark Maroon for the top item(s)
    maroon_dark = '#98002e'
    
    # Palette for the rest: Light Red/Pink shades + Warm Greys
    # User wanted "very light red" mixed with greys
    secondary_palette = [
        '#CF8ea2', # Light dusty red
        '#D6D6D6', # Light grey
        '#C9A0A5', # Medium rosy pink
        '#B0B0B0', # Medium grey
        '#E8DED1', # That beige/tan tone from the template
        '#999999'  # Darker grey
    ]
    
    # Assign colors
    colors = []
    for i in range(len(counts)):
        if i == 0:
            colors.append(maroon_dark)
        else:
            # Cycle through the secondary palette
            colors.append(secondary_palette[(i - 1) % len(secondary_palette)])

    # 3. Generate Treemap Coordinates
    # Canvas size 0..100
    x_min, y_min, x_max, y_max = 0, 0, 100, 50 # Aspect ratio 2:1 roughly for the graph area
    
    # Normalize
    norm_counts = squarify.normalize_sizes(counts, x_max - x_min, y_max - y_min)
    rects = squarify.squarify(norm_counts, x_min, y_min, x_max - x_min, y_max - y_min)

    # 4. Draw
    fig = plt.figure(figsize=(10, 5), facecolor='#F4F4F4') # Updated background color
    ax = fig.add_subplot(111)
    
    # Hide axes
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)
    ax.axis('off')

    # Styling constants
    GAP = 1.0  # Gap between blocks
    ROUNDING = 2.0  # Rounding radius
    
    for rect, item, color in zip(rects, items, colors):
        x, y, dx, dy = rect['x'], rect['y'], rect['dx'], rect['dy']
        
        # Shrink rectangle to create gaps
        if dx > GAP:
            x += GAP / 2
            dx -= GAP
        if dy > GAP:
            y += GAP / 2
            dy -= GAP

        # Draw Rounded Rectangle using FancyBboxPatch
        fancy_box = patches.FancyBboxPatch(
            (x, y), dx, dy,
            boxstyle=f"Round,pad=0,rounding_size={ROUNDING}",
            linewidth=0,
            edgecolor=None,
            facecolor=color,
            alpha=1.0,
            mutation_scale=1
        )
        ax.add_patch(fancy_box)
        
        # 5. Text Labeling with logic
        # Show text even if small, but scale it down
        # Minimum reasonable size to display ANYTHING
        if dx > 4 and dy > 4: 
            # Center point
            cx = x + dx/2
            cy = y + dy/2
            
            # Text color contrast
            if color == maroon_dark or color == '#999999':
                txt_color = 'white'
            else:
                txt_color = '#4A4A4A' # Dark gray for clarity

            # Dynamic Font Sizing
            # Calculate base size from smallest dimension to ensure fit
            box_min_dim = min(dx, dy)
            
            # Count size: ~40% of min dimension, clamped between 10 and 28
            count_fs = max(28, min(10, box_min_dim * 0.45))
            
            # Label size: ~15% of min dimension, clamped between 7 and 14
            label_fs = max(7, box_min_dim * 0.6)
                
            # Render COUNT (Big number on top)
            # Position slightly above center
            # Wrapper for text
            # Allow wider text block (closer to edges)
            wrap_width = max(15, int(dx / 2.7)) # allowed closer to edges
            wrapped_label = textwrap.fill(item['name'], width=wrap_width)
            
            # Position offsets - Symmetric around center (cy)
            # Gap of 0.06 total (3% up, 3% down from center)
            # Count sits on line slightly above center
            ax.text(cx, cy + (dy * 0.03), str(item['count']),
                    ha='center', va='bottom', 
                    fontsize=count_fs,
                    fontweight='heavy',
                    fontfamily='sans-serif',
                    color=txt_color)
            
            # Name hangs from line slightly below center
            ax.text(cx, cy - (dy * 0.03), wrapped_label,
                    ha='center', va='top', 
                    fontsize=label_fs,
                    fontweight='bold', 
                    fontfamily='sans-serif',
                    color=txt_color)
    
    plt.tight_layout()
    
    if return_base64:
        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=300, bbox_inches='tight')
        plt.close()
        buf.seek(0)
        return base64.b64encode(buf.read()).decode('utf-8')
    else:
        plt.savefig(output_path, dpi=300, bbox_inches='tight')
        plt.close()
        return output_path
