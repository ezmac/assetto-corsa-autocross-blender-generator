import os
import datetime

def generate_ini_content(data):
    # Logic to generate ini_content based on the provided data
    ini_content = """[HEADER]\nGenerated on April 24, 2025\n...\n"""
    return ini_content

def generate_ini_file(content, path):
    """Write content to an INI file"""
    with open(path, 'w') as f:
        f.write(content)
    print(f"Generated INI file: {path}")

def generate_ini_content(track_name="AutoCross"):
    """Generate content for an INI file with default template values"""
    current_date = datetime.datetime.now().strftime("%m/%d/%Y %I:%M:%S %p")
    
    content = "[HEADER]\n"
    content += "VERSION=2\n"
    content += f"TRACK={track_name}\n"
    content += "AUTHOR=AutoCrossCone\n"
    content += f"DATE={current_date}\n\n"
    
    return content

def generate_race_ini(output_path, track_name="AutoCross"):
    """
    Generate race.ini file for Assetto Corsa track
    
    Args:
        output_path: Path to save the race.ini file
        track_name: Name of the track
    """
    # Create the ini content
    content = generate_ini_content(track_name)
    
    # Add car list section
    content += "[CAR_0]\n"
    content += "MODEL=ks_abarth500\n"  # Default car
    content += "SKIN=0_rosso\n"
    content += "SPECTATOR_MODE=0\n"
    content += "DRIVERNAME=\n"
    content += "TEAM=\n"
    content += "GUID=\n"
    content += "BALLAST=0\n"
    content += "RESTRICTOR=0\n\n"
    
    # You can add more cars if desired
    content += "[CAR_1]\n"
    content += "MODEL=ks_mazda_mx5_nd\n"  # Another good car for autocross
    content += "SKIN=12_ceramic\n"
    content += "SPECTATOR_MODE=0\n"
    content += "DRIVERNAME=\n"
    content += "TEAM=\n"
    content += "GUID=\n"
    content += "BALLAST=0\n"
    content += "RESTRICTOR=0\n\n"
    
    # Write the file
    generate_ini_file(content, output_path)