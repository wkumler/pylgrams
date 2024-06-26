
from lxml import etree
import zlib
import base64
import struct
import math
import numpy as np
import pandas as pd
import os
import time

def grabMzmlData(filename, grab_what, verbosity=0):
    """Get mass-spectrometry data from an mzML file

    This function handles the mzML side of things, reading in files that are
    written in the mzML format. Much of the code is similar to the mzXML format,
    but the xpath handles are different and the mz/int array is encoded as two
    separate entries rather than simultaneously. This function has been exposed
    to the user in case per-file optimization (such as peakpicking or additional
    filtering) is desired before the full data object is returned.

    Parameters
    ----------
    files: A character vector of filenames to read into memory.
    Both absolute and relative paths are acceptable.

    grab_what: What data should be read from the file? Options include
    "MS1" for data only from the first spectrometer, "MS2" for fragmentation
    data, "BPC" for a base peak chromatogram, or "TIC" for a total ion chromatogram.
    These options can be combined (i.e. `grab_data=["MS1", "MS2"]`) or
    this argument can be set to "everything" to extract all of the above.

    verbosity: Three levels of processing output to the console are
    available, with increasing verbosity corresponding to higher integers. A
    verbosity of zero means that no output will be produced, useful when
    wrapping within larger functions. A verbosity of 1 will produce a progress
    bar that updates after each file is read. A verbosity of 2 or higher will
    produce timing output for each individual file read in. The default, NULL,
    will select between 1 and 2 depending on the number of files being read: if
    a single file, verbosity is set to 2; if multiple files, verbosity is set
    to 1.

    Returns
    -------
    A list of Pandas data frames, each named after the arguments requested in
    grab_what. E.g. ["MS1"] contains MS1 information, ["MS2"] contains fragmentation
    info, etc. MS1 data has four columns: retention time (rt), mass-to-charge
    (mz), intensity (int), and filename. MS2 data has six: retention time (rt),
    precursor m/z (premz), fragment m/z (fragmz), fragment intensity (int),
    collision energy (voltage), and filename. Data
    requested that does not exist in the provided files (such as MS2 data
    requested from MS1-only files) will return an empty (length zero)
    data frame.

    """
    if verbosity > 1:
        print(f"\nReading file {os.path.basename(filename)}... ")
        last_time = time.time()

    xml_data = etree.parse(filename)
    output_data = {}
    file_metadata = grabMzmlEncodingData(xml_data)

    if "MS1" in grab_what:
        if verbosity > 1:
            last_time = timeReport(last_time, "Reading MS1 data...")
        output_data["MS1"] = grabMzmlMS1(xml_data, file_metadata)

    if "MS2" in grab_what:
        if verbosity > 1:
            last_time = timeReport(last_time, "Reading MS2 data...")
        output_data["MS2"] = grabMzmlMS2(xml_data, file_metadata)

    if "BPC" in grab_what:
        if verbosity > 1:
            last_time = timeReport(last_time, "Reading BPC data...")
        output_data["BPC"] = grabMzmlBPC(xml_data)

    if "TIC" in grab_what:
        if verbosity > 1:
            last_time = timeReport(last_time, "Reading TIC data...")
        output_data["TIC"] = grabMzmlBPC(xml_data, TIC=True)

    if verbosity > 1:
        time_total = round(time.time() - last_time, 2)
        print("Total time:", time_total, "seconds")

    return output_data

def grabMzmlBPC(xml_data, TIC=False):
    if TIC == True:
        cvparam_name = "total ion current"
    else:
        cvparam_name = "base peak intensity"

    ms1_xpath = "//d1:spectrum[d1:cvParam[@name=\"ms level\" and @value=\"1\"]][d1:cvParam[@name=\"" + cvparam_name + "\"]]"
    ms1_nodes = xml_data.xpath(ms1_xpath, namespaces={'d1': 'http://psi.hupo.org/ms/mzml'})
    rt_vals = grabSpectraRt(ms1_nodes)

    int_xpath = 'd1:cvParam[@name="' + cvparam_name + '"]'
    int_nodes = [node.xpath(int_xpath, namespaces={'d1': 'http://psi.hupo.org/ms/mzml'}) for node in ms1_nodes]
    int_nodes = [item for sublist in int_nodes for item in sublist]
    int_vals = [float(node.attrib["value"]) for node in int_nodes]

    all_data = pd.DataFrame({
        'rt': rt_vals,
        'int': int_vals
    })
    return(all_data)


def grabMzmlEncodingData(xml_data):
    ns = {'d1': 'http://psi.hupo.org/ms/mzml'}

    init_xpath = "//*[self::d1:spectrum or self::d1:chromatogram]"
    init_node = xml_data.xpath(init_xpath, namespaces=ns)
    if not init_node:
        raise ValueError("Unable to find a spectrum or chromatogram node from which to extract metadata")

    init_node = init_node[0]

    compr_xpath = '//d1:cvParam[@accession="MS:1000574" or @accession="MS:1000576"]'
    compr_node = init_node.xpath(compr_xpath, namespaces=ns)[0]
    compr_type = compr_node.attrib["name"]

    compr = {
        "zlib": "gzip",
        "zlib compression": "gzip",
        "no compression": "none",
        "none": "none"
    }[compr_type]

    mz_precision_xpath = '//d1:cvParam[@accession="MS:1000523"]'
    mz_bit_node = init_node.xpath(mz_precision_xpath, namespaces=ns)[0]
    mz_bit_type = mz_bit_node.attrib["name"]
    mz_precision = int(mz_bit_type.split("-")[0]) / 8

    int_bit_xpath = '//d1:cvParam[@accession="MS:1000521"]'
    int_bit_node = init_node.xpath(int_bit_xpath, namespaces=ns)
    if int_bit_node:
        int_bit_node = int_bit_node[0]
        int_bit_type = int_bit_node.attrib["name"]
        int_precision = int(int_bit_type.split("-")[0]) / 8
    else:
        int_precision = mz_precision

    # Ensure mz_precision and int_precision are not NaN
    mz_precision = mz_precision if not math.isnan(mz_precision) else int_precision
    int_precision = int_precision if not math.isnan(int_precision) else mz_precision

    return {
        "compression": compr,
        "mz_precision": int(mz_precision),
        "int_precision": int(int_precision),
        "endi_enc": "little"
    }

def grabMzmlMS1(xml_data, file_metadata):
    ms1_xpath = "//d1:spectrum[d1:cvParam[@name=\"ms level\" and @value=\"1\"]]"
    ms1_nodes = xml_data.xpath(ms1_xpath, namespaces={'d1': 'http://psi.hupo.org/ms/mzml'})
    
    if not ms1_nodes:
        return pd.DataFrame({'rt': [], 'mz': [], 'int': []})
    
    rt_vals = grabSpectraRt(ms1_nodes)
    mz_vals = grabSpectraMz(ms1_nodes, file_metadata)
    int_vals = grabSpectraInt(ms1_nodes, file_metadata)
    
    all_data = pd.DataFrame({
        'rt': np.repeat(rt_vals, [len(x) for x in mz_vals]),
        'mz': np.concatenate(mz_vals),
        'int': np.concatenate(int_vals)
    })
    
    return all_data

def grabMzmlMS2(xml_data, file_metadata):
    ms2_xpath = "//d1:spectrum[d1:cvParam[@name=\"ms level\" and @value=\"2\"]]"
    ms2_nodes = xml_data.xpath(ms2_xpath, namespaces={'d1': 'http://psi.hupo.org/ms/mzml'})
    
    if not ms2_nodes:
        return pd.DataFrame({'rt': [], 'premz': [], 'fragmz': [], 'int': [], 'voltage': []})
    
    rt_vals = grabSpectraRt(ms2_nodes)
    premz_vals = grabSpectraPremz(ms2_nodes)
    voltage = grabSpectraVoltage(ms2_nodes)
    mz_vals = grabSpectraMz(ms2_nodes, file_metadata)
    int_vals = grabSpectraInt(ms2_nodes, file_metadata)
    
    all_data = {
        'rt': np.concatenate([[rt_val] * len(mz_val) for rt_val, mz_val in zip(rt_vals, mz_vals)]),
        'premz': np.concatenate([[premz_val] * len(mz_val) for premz_val, mz_val in zip(premz_vals, mz_vals)]),
        'fragmz': np.concatenate(mz_vals),
        'int': np.concatenate(int_vals),
        'voltage': np.concatenate([[voltage_val] * len(mz_val) for voltage_val, mz_val in zip(voltage, mz_vals)])
    }
    
    return pd.DataFrame(all_data)

def grabSpectraRt(xml_nodes):
    rt_xpath = './/d1:scanList/d1:scan/d1:cvParam[@name="scan start time"]'
    ns = {'d1': 'http://psi.hupo.org/ms/mzml'}
    
    # Find all the relevant nodes
    rt_nodes = [node.xpath(rt_xpath, namespaces=ns) for node in xml_nodes]
    rt_nodes = [item for sublist in rt_nodes for item in sublist]
    
    # Extract the unit names and values
    rt_units = set(node.attrib.get('unitName') for node in rt_nodes)
    rt_vals = [float(node.attrib['value']) for node in rt_nodes]

    # Convert to minutes if not already in minutes
    if "minute" not in rt_units:
        rt_vals = [val / 60 for val in rt_vals]

    return rt_vals

def grabSpectraMz(xml_nodes, file_metadata):
    mz_xpath = './/d1:binaryDataArrayList/d1:binaryDataArray[1]/d1:binary'
    ns = {'d1': 'http://psi.hupo.org/ms/mzml'}
    
    mz_vals = [node.xpath(mz_xpath, namespaces=ns) for node in xml_nodes]
    mz_vals = [item for sublist in mz_vals for item in sublist]  # Flatten the list
    mz_vals = [node.text for node in mz_vals]
    
    result = []
    for binary in mz_vals:
        if not binary:
            result.append([])
            continue
        decoded_binary = base64.b64decode(binary)
        raw_binary = bytes(decoded_binary)
        if file_metadata['compression'] == 'none':
            decomp_binary = raw_binary
        elif file_metadata['compression'] == 'zlib':  # Assuming zlib compression
            decomp_binary = zlib.decompress(raw_binary)
        elif file_metadata['compression'] == 'gzip':
            decomp_binary = zlib.decompress(raw_binary)
        else:
            raise ValueError(f"Unsupported compression type: {file_metadata['compression']}")
        # Read the binary data as doubles (8 bytes for each double value)
        num_doubles = len(decomp_binary) // file_metadata['mz_precision']
        double_format = '{}d'.format(int(num_doubles))
        unpacked_data = struct.unpack(double_format, decomp_binary)
        result.append(unpacked_data)
    return result

def grabSpectraInt(xml_nodes, file_metadata):
    int_xpath = './/d1:binaryDataArrayList/d1:binaryDataArray[2]/d1:binary'
    ns = {'d1': 'http://psi.hupo.org/ms/mzml'}
    
    int_vals = [node.xpath(int_xpath, namespaces=ns) for node in xml_nodes]
    int_vals = [item for sublist in int_vals for item in sublist]  # Flatten the list
    int_vals = [node.text for node in int_vals]
    
    result = []
    for binary in int_vals:
        if not binary:
            result.append([])
            continue
        decoded_binary = base64.b64decode(binary)
        raw_binary = bytes(decoded_binary)
        if file_metadata['compression'] == 'none':
            decomp_binary = raw_binary
        elif file_metadata['compression'] == 'zlib':
            decomp_binary = zlib.decompress(raw_binary)
        elif file_metadata['compression'] == 'gzip':
            decomp_binary = zlib.decompress(raw_binary)
        else:
            raise ValueError(f"Unsupported compression type: {file_metadata['compression']}")
        num_doubles = len(decomp_binary) // file_metadata['int_precision']
        double_format = '{}f'.format(int(num_doubles))
        unpacked_data = struct.unpack(double_format, decomp_binary)
        result.append(unpacked_data)
    return result

def grabSpectraVoltage(xml_nodes):
    volt_xpath = "//d1:precursorList/d1:precursor/d1:activation/d1:cvParam[@name=\"collision energy\"]"
    ns = {'d1': 'http://psi.hupo.org/ms/mzml'}
    volt_nodes = [node.xpath(volt_xpath, namespaces=ns) for node in xml_nodes]
    volt_nodes = [item for sublist in volt_nodes for item in sublist]
    if not volt_nodes:
        return [None] * len(xml_nodes)
    volt_vals = [int(node.get("value")) for node in volt_nodes]
    return(volt_vals)

def grabSpectraPremz(xml_nodes):
    premz_xpath = "//d1:precursorList/d1:precursor/d1:selectedIonList/d1:selectedIon/d1:cvParam[@name=\"selected ion m/z\"]"
    ns = {'d1': 'http://psi.hupo.org/ms/mzml'}
    premz_nodes = [node.xpath(premz_xpath, namespaces=ns) for node in xml_nodes]
    premz_nodes = [item for sublist in premz_nodes for item in sublist]
    premz_vals = [float(node.get("value")) for node in premz_nodes]
    return(premz_vals)

def timeReport(last_time, text):
    current_time = time.time()
    time_total = round(current_time - last_time, 2)
    print(text, time_total, "seconds")
    return current_time

__all__ = ["grabMzmlData"]
