import os

def generate_fries_svgs():
    """Generate SVG images for each flavor with transparent background"""
    
    flavors = {
        'cheese': '#FFD700',  # Gold
        'sour-cream': '#F5F5F5',  # White Smoke
        'bbq': '#8B4513',  # Brown
        'chili-bbq': '#CD5C5C',  # Indian Red
        'wasabi': '#98FB98',  # Pale Green
        'white-cheddar': '#FFFACD',  # Lemon Chiffon
        'chili-powder': '#FF4500',  # Orange Red
        'salted-caramel': '#D2691E'  # Chocolate
    }
    
    # Create fries directory if it doesn't exist
    os.makedirs('static/images/fries', exist_ok=True)
    
    for flavor, color in flavors.items():
        svg_content = f'''<?xml version="1.0" encoding="UTF-8"?>
<svg width="200" height="200" viewBox="0 0 200 200" xmlns="http://www.w3.org/2000/svg">
    <!-- No background - transparent -->
    
    <!-- Fries Container (Box) - Semi-transparent -->
    <rect x="40" y="100" width="120" height="60" fill="#FF6B00" opacity="0.9" rx="10"/>
    <rect x="40" y="90" width="120" height="20" fill="#FF8C42" opacity="0.9" rx="5"/>
    
    <!-- Fries - Solid colors, no background -->
    <rect x="50" y="40" width="15" height="70" fill="{color}" rx="3" transform="rotate(-5 57 75)"/>
    <rect x="70" y="35" width="15" height="75" fill="{color}" rx="3" transform="rotate(-2 77 72)"/>
    <rect x="90" y="30" width="15" height="80" fill="{color}" rx="3"/>
    <rect x="110" y="35" width="15" height="75" fill="{color}" rx="3" transform="rotate(2 117 72)"/>
    <rect x="130" y="40" width="15" height="70" fill="{color}" rx="3" transform="rotate(5 137 75)"/>
    
    <!-- Fry Details (lines) -->
    <line x1="55" y1="55" x2="55" y2="85" stroke="#000000" stroke-width="1" opacity="0.1"/>
    <line x1="75" y1="50" x2="75" y2="85" stroke="#000000" stroke-width="1" opacity="0.1"/>
    <line x1="97" y1="45" x2="97" y2="85" stroke="#000000" stroke-width="1" opacity="0.1"/>
    <line x1="117" y1="50" x2="117" y2="85" stroke="#000000" stroke-width="1" opacity="0.1"/>
    <line x1="137" y1="55" x2="137" y2="85" stroke="#000000" stroke-width="1" opacity="0.1"/>
    
    <!-- Flavor Text -->
    <text x="100" y="170" font-family="Arial" font-size="14" fill="#333" text-anchor="middle" font-weight="bold">{flavor.replace('-', ' ').title()}</text>
    
    <!-- Steam (optional) - Very light -->
    <path d="M70 20 Q80 10, 90 20 Q100 30, 110 20 Q120 10, 130 20" stroke="#CCCCCC" fill="none" stroke-width="2" opacity="0.3"/>
</svg>'''
        
        filename = f"static/images/fries/{flavor}.svg"
        with open(filename, 'w') as f:
            f.write(svg_content)
        print(f"Generated: {filename}")

if __name__ == "__main__":
    generate_fries_svgs()
    print("\n✅ All fries images generated successfully with transparent backgrounds!")
    print("📁 Location: static/images/fries/")