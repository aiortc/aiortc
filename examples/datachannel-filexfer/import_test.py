import sys
import os
from os import path
sys.path.append(path.dirname(path.abspath(__file__)) + "/../../")

os.environ['AIORTC_SPECIAL_MODE'] = 'DC_ONLY'

import aiortc
