import logging
from pathlib import Path
from shutil import copyfile, move, rmtree, unpack_archive
from tempfile import mkdtemp
from os import mkdir, unlink, curdir
from json import load, dump
from abc import ABC, abstractmethod
import time
import subprocess
from types import SimpleNamespace
from urllib import request
from math import floor

logging.getLogger().setLevel(logging.INFO)

class Settings(SimpleNamespace):
    """
    Interface to the settings file
    """

    FILENAME = '../settings.json' # path relative to the scripts/ folder

    def open_as_dict() -> dict():
        settings = dict()
        with open(Settings.FILENAME) as settings_file:
            settings = load(settings_file)
        return settings
    
    def path(name : str) -> Path:
        # open settings as dict, get selected entry in 'paths', convert to Path, expand '~' to user home
        return Path.expanduser(Path(Settings.open_as_dict()['paths'][name])).absolute()

class UserInput():
    """
    Interface to user input frequently needed (ex: "Do you want to overwrite ?")
    allowing to memorize the answer, skipping next user inputs
    """
    def __init__(self):
        self.memorized_answer = None

    def ask_and_memorize(self,question):
        """
        Ask a confirmation to the user and allow to memorize the answer:

        'always' = yes for all

        'never' = no for all
        """
        if self.memorized_answer!=None:
            return self.memorized_answer
        user_choice = ''
        while user_choice not in ['y','yes','n','no','always','never']:
            user_choice = input(question + ' [yes/no/always/never] ').lower()
        if user_choice == 'always':
            self.memorized_answer = True
        elif user_choice == 'never':
            self.memorized_answer = False
        return ((user_choice == "y") | (user_choice == "yes") | (user_choice == "always"))

    def forget_memorized_answer(self):
        self.memorized_answer = None

    # @classmethod 
    def ask(question):
        """
        Ask a confirmation to the user without allowing to memorize the answer
        """
        user_choice = ''
        while user_choice not in ['y','yes','n','no']:
            user_choice = input(question + ' [yes/no] ').lower()
        return ((user_choice[0] == "y"))

class ParametricString:
    """
    A string with named parameters, filled later. Example:

    command_line = ParametricString('executable -r -v {input} --set-option {option_name}')

    command_line.assemble(input='in.txt',option_name='legacy')
    """
    def __init__(self,string_template):
        self.string_template = string_template # will be formatted in assemble()
        self.parameters = list()
        string_template = string_template.split("{")
        for i in range(1,len(string_template)): # ommit the fist element, which doesn't start with '{'
            if "}" not in string_template[i]:
                raise Exception('Unbalanced curly brackets in "' + self.string_template + '"')
            self.parameters.append(string_template[i].split("}")[0]) # cut at '}' and keep the first part

    def get_parameters(self) -> list:
        return self.parameters
    
    def assemble(self,check_unused: bool,**kwargs) -> str:
        # check arguments
        for parameter in self.parameters:
            if parameter not in kwargs:
                raise Exception("argument named '{}' is missing in assemble()".format(parameter))
        if check_unused:
            for arg in kwargs:
                if arg not in self.parameters:
                    raise Exception("""argument named '{}' was given to assemble()
                                    but is not in the string template given at the initialization of ParametricString:
                                    '{}' """"".format(arg,self.parameters))
        # return assembled string
        return self.string_template.format(**kwargs)

class CollectionsManager():
    """
    Manage entries collections, stored in collections.json
    """
    def __init__(self,datafolder: Path):
        collections_filename = datafolder / 'collections.json' # TODO move collections manager inside root class, no need to duplicate self.datafolder and 'collections.json'
        if not collections_filename.exists():
            with open(collections_filename,'w') as file:
                dump(dict(), file)# write empty JSON
        self.json = load(open(collections_filename))
        self.datafolder = datafolder

    def collections(self) -> list:
        return self.json.keys()

    def append_to_collection(self,collection_name,element: str):
        # element is either an existing collection, of a path (relative to self.datafolder) to a folder
        if (element not in self.json.keys()) & ((self.datafolder / element).exists()==False):
            raise Exception(element + ' is neither an existing collection nor an existing subfolder\nExisting collections : {}'.format(self.json.keys()))
        if collection_name not in self.json.keys():
            self.json[collection_name] = [] # empty list
        self.json[collection_name].append(element)

    def save(self):
        with open(self.datafolder / 'collections.json','w') as file:
            dump(self.json, file, sort_keys=True, indent=4)

def simple_human_readable_duration(duration_seconds) -> str:
    """
    Return a human-readable text (str) for a given duration in seconds:
    hours, minutes & seconds elapsed
    """
    hours   = floor(duration_seconds // 3600)
    minutes = floor(duration_seconds % 3600 // 60)
    seconds = floor(duration_seconds % 60) if duration_seconds > 60 else round(duration_seconds % 60,3) # high precision only for small durations
    formatted_duration = ''
    if hours != 0:
        formatted_duration += '{}h '.format(hours)
    if minutes != 0 or hours != 0:
        formatted_duration += '{}m '.format(minutes)
    formatted_duration += '{}s'.format(seconds)
    return formatted_duration

def GenerativeAlgorithm(name: str, input_folder, executable: Path, executable_arugments: str, name_template: str, inside_subfolder: list, **kwargs):
    """
    Define and execute a generative algorithm, that is an algorithm on a data folder which creates a subfolder.
    Wrap an executable and manage command line assembly from parameters, chrono, stdout/stderr files and write a JSON file will all the info.
    """
    executable_arugments = ParametricString(executable_arugments)
    name_template = ParametricString(name_template)
    for parameter in name_template.get_parameters():
        if parameter not in executable_arugments.get_parameters():
            raise Exception("'" + parameter + "' is not a parameter of the executable, so it cannot be a part of the subfolder filename")
    for parameter in inside_subfolder:
        if parameter not in executable_arugments.get_parameters():
            raise Exception("'" + parameter + "' is not a parameter of the executable, so it cannot specified as inside the subfolder")
        if parameter in name_template.get_parameters():
            raise Exception("'" + parameter + "' is specified as inside the subfolder, so it cannot be a part of the name of the subfolder")
    start_datetime = time.localtime()
    start_datetime_iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', start_datetime)
    # ISO 8601 cannot be used in the subfolder filename, because ':' is forbidden on Windows
    start_datetime_filesystem = time.strftime('%Y%m%d_%H%M%S', start_datetime)
    # Assemble name of to-be-created subfolder
    subfolder_name = name_template.assemble(False,**kwargs).replace('%d',start_datetime_filesystem)
    # Check there is no subfolder with this name
    if (input_folder / subfolder_name).exists():
        raise Exception("Already a subfolder named '{}'".format(subfolder_name))
    # Create the subfolder
    mkdir(input_folder / subfolder_name)
    # add subfolder name as prefix for subset of kwargs, given by inside_subfolder
    for k,v in kwargs.items():
        if k in inside_subfolder:
            kwargs[k] = str((input_folder / subfolder_name / v).absolute())
    # Assemble command string
    # TODO check if the executable exists
    command = str(executable.absolute()) + " " + executable_arugments.assemble(False,**kwargs)
    # Write parameters value in a dict (will be dumped as JSON)
    info_file = dict()
    info_file[start_datetime_iso] = {
        'GenerativeAlgorithm': name,
        'command': command,
        'parameters': dict()
    }
    for k,v in kwargs.items():
        info_file[start_datetime_iso]['parameters'][k] = v
    # Start chrono, call executable and store stdout/stderr
    chrono_start = time.monotonic()
    completed_process = subprocess.run(command, shell=True, capture_output=True)
    chrono_stop = time.monotonic()
    # write stdout and stderr
    if completed_process.stdout != b'': # if the subprocess wrote something in standard output
        filename = name + '.stdout.txt'
        f = open(input_folder / subfolder_name / filename,'xb')# x = create new file, b = binary mode
        f.write(completed_process.stdout)
        f.close()
        info_file[start_datetime_iso]['stdout'] = filename
    if completed_process.stderr != b'': # if the subprocess wrote something in standard error
        filename =  name + '.stderr.txt'
        f = open(input_folder / subfolder_name / filename,'xb')
        f.write(completed_process.stderr)
        f.close()
        info_file[start_datetime_iso]['stderr'] = filename
    # store return code and duration
    info_file[start_datetime_iso]["return_code"] = completed_process.returncode
    duration = chrono_stop - chrono_start
    info_file[start_datetime_iso]["duration"] = [duration, simple_human_readable_duration(duration)]
    # write JSON file
    with open(input_folder / subfolder_name / 'info.json','w') as file:
            dump(info_file, file, sort_keys=True, indent=4)
    #self.completed_process.check_returncode()# will raise a CalledProcessError if non-zero
    return input_folder / subfolder_name

def InteractiveGenerativeAlgorithm(name: str, input_folder, executable: Path, executable_arugments: str, name_template: str = None, inside_subfolder: list = [], **kwargs):
    """
    Define and execute an interactive generative algorithm, that is an interactive algorithm on a data folder which creates a subfolder (optional).
    Wrap an executable and manage command line assembly from parameters.
    """
    executable_arugments = ParametricString(executable_arugments)
    start_datetime = time.localtime()
    start_datetime_iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', start_datetime)
    # ISO 8601 cannot be used in the subfolder filename, because ':' is forbidden on Windows
    start_datetime_filesystem = time.strftime('%Y%m%d_%H%M%S', start_datetime)
    if name_template != None:
        name_template = ParametricString(name_template)
        for parameter in name_template.get_parameters():
            if parameter not in executable_arugments.get_parameters():
                raise Exception("'" + parameter + "' is not a parameter of the executable, so it cannot be a part of the subfolder filename")
        for parameter in inside_subfolder:
            # if parameter not in executable_arugments.get_parameters(): -> not checked for an interactive algorithm
            if parameter in name_template.get_parameters():
                raise Exception("'" + parameter + "' is specified as inside the subfolder, so it cannot be a part of the name of the subfolder")
        # Assemble name of to-be-created subfolder
        subfolder_name = name_template.assemble(False,**kwargs).replace('%d',start_datetime_filesystem)
        # Check there is no subfolder with this name
        if (input_folder / subfolder_name).exists():
            raise Exception("Already a subfolder named '{}'".format(subfolder_name))
        # Create the subfolder
        mkdir(input_folder / subfolder_name)
        # add subfolder name as prefix for subset of kwargs, given by inside_subfolder
        # also print help message about expected output files location
        print(f'You must save the output file(s) as:')
        for k,v in kwargs.items():
            if k in inside_subfolder:
                kwargs[k] = str((input_folder / subfolder_name / v).absolute())
                print(kwargs[k])
        print(f'then close the window to stop the timer.')
    # else: name_template is None, no output will be stored

    # Assemble command string
    # TODO check if the executable exists
    command = str(executable.absolute()) + " " + executable_arugments.assemble(False,**kwargs) # False because kwargs can be bigger than executable_arugments.get_parameters() for an interactive algorithm

    info_file = dict()
    if name_template != None:
        start_datetime_iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.localtime())
        info_file[start_datetime_iso] = {
            'InteractiveGenerativeAlgorithm': name,
            'command': command,
            'parameters': dict()
        }
        for k,v in kwargs.items():
            info_file[start_datetime_iso]['parameters'][k] = v

    # Start chrono, call executable and store stdout/stderr
    chrono_start = time.monotonic()
    completed_process = subprocess.run(command, shell=True, capture_output=(name_template != None))
    chrono_stop = time.monotonic()

    if name_template != None:
        # write stdout and stderr
        if completed_process.stdout != b'': # if the subprocess wrote something in standard output
            filename = name + '.stdout.txt'
            f = open(input_folder / subfolder_name / filename,'xb')# x = create new file, b = binary mode
            f.write(completed_process.stdout)
            f.close()
            info_file[start_datetime_iso]['stdout'] = filename
        if completed_process.stderr != b'': # if the subprocess wrote something in standard error
            filename =  name + '.stderr.txt'
            f = open(input_folder / subfolder_name / filename,'xb')
            f.write(completed_process.stderr)
            f.close()
            info_file[start_datetime_iso]['stderr'] = filename
        # store return code and duration
        info_file[start_datetime_iso]["return_code"] = completed_process.returncode
        duration = chrono_stop - chrono_start
        info_file[start_datetime_iso]["duration"] = [duration, simple_human_readable_duration(duration)]
        # write JSON file
        with open(input_folder / subfolder_name / 'info.json','w') as file:
                dump(info_file, file, sort_keys=True, indent=4)
        #self.completed_process.check_returncode()# will raise a CalledProcessError if non-zero
        return input_folder / subfolder_name
    else:
        return None # no subfolder created

def TransformativeAlgorithm(name: str, input_folder, executable: Path, executable_arugments: str, **kwargs):
    """
    Define and execute a transformative algorithm, that is an algorithm modifying a data folder without creating a subfolder.
    Wrap an executable and manage command line assembly from parameters, chrono, stdout/stderr files and write a JSON file will all the info.
    """
    executable_arugments = ParametricString(executable_arugments)
    # Assemble command string
    # TODO check if the executable exists
    command = str(executable.absolute()) + " " + executable_arugments.assemble(False,**kwargs)
    # Read JSON file
    info_file = dict()
    if not (input_folder / 'info.json').exists():
        logging.warning('Cannot find info.json in ' + str(input_folder))
    else:
        info_file = load(open(input_folder / 'info.json'))
        assert (len(info_file) != 0)
    # Write parameters in the dict (will be dumped as JSON)
    start_datetime_iso = ''
    while 1:
        start_datetime_iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.localtime())
        if start_datetime_iso not in info_file.keys():
            break
        # else : already a key with this datetime (can append with very fast algorithms)
        time.sleep(1.0)
    info_file[start_datetime_iso] = {
        'TransformativeAlgorithm': name,
        'command': command,
        'parameters': dict()
    }
    for k,v in kwargs.items():
        info_file[start_datetime_iso]['parameters'][k] = v
    # Start chrono, call executable and store stdout/stderr
    chrono_start = time.monotonic()
    completed_process = subprocess.run(command, shell=True, capture_output=True)
    chrono_stop = time.monotonic()
    # write stdout and stderr
    if completed_process.stdout != b'': # if the subprocess wrote something in standard output
        filename = name + '.stdout.txt'
        f = open(input_folder / filename,'xb')# x = create new file, b = binary mode
        f.write(completed_process.stdout)
        f.close()
        info_file[start_datetime_iso]['stdout'] = filename
    if completed_process.stderr != b'': # if the subprocess wrote something in standard error
        filename =  name + '.stderr.txt'
        f = open(input_folder / filename,'xb')
        f.write(completed_process.stderr)
        f.close()
        info_file[start_datetime_iso]['stderr'] = filename
    # store return code and duration
    info_file[start_datetime_iso]["return_code"] = completed_process.returncode
    duration = chrono_stop - chrono_start
    info_file[start_datetime_iso]["duration"] = [duration, simple_human_readable_duration(duration)]
    # write JSON file
    with open(input_folder / 'info.json','w') as file:
        dump(info_file, file, sort_keys=True, indent=4)
    #self.completed_process.check_returncode()# will raise a CalledProcessError if non-zero

def rename_file(input_folder, old_filename: str, new_filename: str):
    assert(old_filename != new_filename)
    # Read JSON file
    info_file = dict()
    if (input_folder / 'info.json').exists():
        info_file = load(open(input_folder / 'info.json'))
        assert (len(info_file) != 0)
    # Write parameters in the dict (will be dumped as JSON)
    start_datetime_iso = ''
    while 1:
        start_datetime_iso = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.localtime())
        if start_datetime_iso not in info_file.keys():
            break
        # else : already a key with this datetime (can append with very fast algorithms)
        time.sleep(1.0)
    info_file[start_datetime_iso] = {
        'TransformativeAlgorithm': 'rename',
        'old_filename': old_filename,
        'new_filename': new_filename
    }
    move(input_folder / old_filename, input_folder / new_filename)
    # write JSON file
    with open(input_folder / 'info.json','w') as file:
        dump(info_file, file, sort_keys=True, indent=4)
    logging.info(f'In {input_folder}, {old_filename} renamed to {new_filename}')


class AbstractDataFolder(ABC):
    """
    Represents an entry of the data folder
    """

    @staticmethod
    @abstractmethod
    def is_instance(path: Path) -> bool:
        raise Exception('Not all AbstractDataFolder subclasses have specialized is_instance()')
    
    @abstractmethod #prevent user from instanciating a AbstractDataFolder
    def __init__(self, path: Path):
        if not path.exists():
            logging.error(str(path) + ' does not exist')
            exit(1)
        self.path = path

    def type(self) -> str:
        return self.__class__.__name__
    
    def __str__(self) -> str:
        return '{{type={}, path={}}}'.format(self.type(),str(self.path))
    
    @abstractmethod
    def view(self, what = None):
        print(self)

    def get_file(self, filename : str, must_exist : bool = False) -> Path:
        return (self.path / filename).absolute()

    # ----- Specific functions of the abstract class --------------------

    @staticmethod
    def type_inference(path: Path):
        infered_types = list() # will store all AbstractDataFolder subclasses recognizing path as an instance
        for subclass in AbstractDataFolder.__subclasses__():
            if subclass.is_instance(path):
                infered_types.append(subclass) # current subclass recognize path
        if len(infered_types) == 0:
            raise Exception('No known class recognize the folder ' + str(path.absolute()))
        elif len(infered_types) > 1: # if multiple AbstractDataFolder subclasses recognize path
            raise Exception('Multiple classes recognize the folder ' + str(path.absolute()) + ' : ' + str([x.__name__ for x in infered_types]))
        else:
            return infered_types[0]

    @staticmethod
    def instantiate(path: Path): # Can this method become AbstractDataFolder.__init__() ??
        """
        Instanciate an AbstractDataFolder subclass by infering the type of the given data folder
        """
        data_folder = Settings.path('data_folder')
        assert(data_folder.is_dir())
        if not Path.relative_to(path,data_folder):
            raise Exception(f'Forbidden instanciation because {path.absolute()} is not inside the current data folder {str(data_folder)} (see {Settings.FILENAME})')
        return (AbstractDataFolder.type_inference(path))(path)
    
# Checklist for creating a subclass = a new kind of data folder
# - for almost all cases, __init__(self,path) just need to call AbstractDataFolder.__init__(self,path)
# - specialize the is_instance(path) static method and write the rule saying if a given folder is an instance of your new type
# - specialize the view() method to visualize the content of theses data folders the way you want
# - name your default visualization and create a class variable named DEFAULT_VIEW. overwrite 'what' argument of view() if it's None
# - enumerate hard coded filenames in inner namespace FILENAMES
# - specialize the get_file() method to detect missing files and potentially auto-compute them
# - create specific methods to add files in your datafolder or to create subfolders

class step(AbstractDataFolder):
    """
    Interface to a step folder
    """

    class FILENAMES(SimpleNamespace):
        STEP = 'CAD.step' # CAD model in the STEP format

    DEFAULT_VIEW = 'step'

    @staticmethod
    def is_instance(path: Path) -> bool:
        return (path / step.FILENAMES.STEP).exists()

    def __init__(self,path: Path, step_file: Path = None):
        # 2 modes
        # - if step_file is None -> create a 'step' class instance interfacing an existing data folder
        # - if step_file is something else -> create the folder, move inside the given STEP file, then instanciate the 'step' class
        path = Path(path)
        if(step_file!=None):
            if path.exists():
                logging.error(str(path) + ' already exists. Overwriting not allowed')
                exit(1)
            mkdir(path)
            copyfile(step_file, path / self.FILENAMES.STEP)
        AbstractDataFolder.__init__(self,path)
        if not (path / self.FILENAMES.STEP).exists():
            logging.error('At the end of step.__init__(), ' + str(path) + ' does not exist')
            exit(1)
    
    def view(self, what = None):
        """
        View STEP file with Mayo
        https://github.com/fougue/mayo
        """
        if what == None:
            what = self.DEFAULT_VIEW
        if what == 'step':
            logging.warning('With Mayo, you may experience infinite loading with NVIDIA drivers. Check __NV_PRIME_RENDER_OFFLOAD and __GLX_VENDOR_LIBRARY_NAME shell variables value.')
            InteractiveGenerativeAlgorithm(
                'view',
                self.path,
                Settings.path('Mayo'),
                '{step} --no-progress', # arguments template
                None,
                [],
                step = str(self.get_file(self.FILENAMES.STEP,True))
            )
        else:
            raise Exception(f'step.view() does not recognize \'what\' value: \'{what}\'')
    
    def get_file(self, filename : str, must_exist : bool = False) -> Path:
        path = super().get_file(filename)
        if (not must_exist) or (must_exist and path.exists()):
            return path
        raise Exception(f'Missing file {str(path)}')
        
    # ----- Generative algorithms (create subfolders) --------------------

    def Gmsh(self,mesh_size) -> Path:
        return GenerativeAlgorithm(
            'Gmsh',
            self.path,
            Settings.path('Gmsh'),
            '{step} -3 -format mesh -o {output_file} -setnumber Mesh.CharacteristicLengthFactor {characteristic_length_factor} -nt {nb_threads}',
            'Gmsh_{characteristic_length_factor}',
            ['output_file'],
            step                            = str(self.get_file(self.FILENAMES.STEP,True)),
            output_file                     = tet_mesh.FILENAMES.TET_MESH_MEDIT,
            characteristic_length_factor    = mesh_size,
            nb_threads                      = 8)

class tet_mesh(AbstractDataFolder):
    """
    Interface to a tet-mesh folder
    """

    class FILENAMES(SimpleNamespace):
        TET_MESH_MEDIT      = 'tet.mesh'        # tetrahedral mesh in the GMF/MEDIT ASCII format
        TET_MESH_VTK        = 'tet_mesh.vtk'    # tetrahedral mesh in the VTK DataFile Version 2.0 ASCII
        SURFACE_MESH_OBJ    = 'surface.obj'     # (triangle) surface of the tet-mesh, in the Wavefront format
        SURFACE_MAP_TXT     = 'surface_map.txt' # association between surface triangles and tet facets (see https://github.com/LIHPC-Computational-Geometry/automatic_polycube/blob/main/app/extract_surface.cpp for the format)

    DEFAULT_VIEW = 'surface_mesh'

    @staticmethod
    def is_instance(path: Path) -> bool:
        return (path / tet_mesh.FILENAMES.TET_MESH_MEDIT).exists()

    def __init__(self,path: Path):
        AbstractDataFolder.__init__(self,Path(path))
    
    def view(self, what = None):
        """
        View files (for now only surface mesh) with Graphite
        https://github.com/BrunoLevy/GraphiteThree
        """
        if what == None:
            what = self.DEFAULT_VIEW
        if what == 'surface_mesh':
            assert((self.path / self.FILENAMES.SURFACE_MESH_OBJ).exists())
            InteractiveGenerativeAlgorithm(
                'view',
                self.path,
                Settings.path('Graphite'),
                '{surface_mesh}', # arguments template
                None,
                [],
                surface_mesh = str(self.get_file(self.FILENAMES.SURFACE_MESH_OBJ,True))
            )
        else:
            raise Exception(f'tet_mesh.view() does not recognize \'what\' value: \'{what}\'')
    
    def get_file(self, filename : str, must_exist : bool = False) -> Path:
        path = super().get_file(filename)
        if (not must_exist) or (must_exist and path.exists()):
            return path
        # so 'file' is missing -> try to auto-compute it
        if filename in [self.FILENAMES.SURFACE_MESH_OBJ, self.FILENAMES.SURFACE_MAP_TXT]:
            self.extract_surface()
            return self.get_file(filename,True)
        if filename == self.FILENAMES.TET_MESH_VTK:
            self.Gmsh_convert_to_VTKv2()
            return self.get_file(filename,True)
        raise Exception(f'Missing file {str(path)}')
    
    # ----- Transformative algorithms (modify current folder) --------------------

    def extract_surface(self):
        assert(not self.get_file(self.FILENAMES.SURFACE_MESH_OBJ).exists())
        assert(not self.get_file(self.FILENAMES.SURFACE_MAP_TXT).exists())
        TransformativeAlgorithm(
            'extract_surface',
            self.path,
            Settings.path('automatic_polycube') / 'extract_surface',
            '{tet_mesh} {surface_mesh} {surface_map}',
            tet_mesh        = str(self.get_file(self.FILENAMES.TET_MESH_MEDIT,      True)),
            surface_mesh    = str(self.get_file(self.FILENAMES.SURFACE_MESH_OBJ         )),
            surface_map     = str(self.get_file(self.FILENAMES.SURFACE_MAP_TXT          ))
        )

    def Gmsh_convert_to_VTKv2(self):
        assert(self.get_file('surface_mesh').exists())
        TransformativeAlgorithm(
            'Gmsh_convert_to_VTKv2',
            self.path,
            Settings.path('Gmsh'),
            '{input} -format vtk -o {output} -save',
            input   = str(self.get_file(self.FILENAMES.TET_MESH_MEDIT,  True)),
            output  = str(self.get_file(self.FILENAMES.TET_MESH_VTK         )),
        )
    
    # ----- Generative algorithms (create subfolders) --------------------

    def naive_labeling(self):
        return GenerativeAlgorithm(
            'naive_labeling',
            self.path,
            Settings.path('automatic_polycube') / 'naive_labeling',
            '{surface_mesh} {labeling}',
            'naive_labeling',
            ['labeling'],
            surface_mesh    = str(self.get_file(self.FILENAMES.SURFACE_MESH_OBJ,    True)),
            labeling        = labeling.FILENAMES.SURFACE_LABELING_TXT
        )
    
    def labeling_painter(self):
        InteractiveGenerativeAlgorithm(
            'labeling_painter',
            self.path,
            Settings.path('automatic_polycube') / 'labeling_painter', 
            '{mesh}', # arguments template
            'labeling_painter_%d',
            ['labeling'],
            mesh        = str(self.get_file(self.FILENAMES.SURFACE_MESH_OBJ,    True)),
            labeling    = labeling.FILENAMES.SURFACE_LABELING_TXT
        )

    def graphcut_labeling(self):
        InteractiveGenerativeAlgorithm(
            'graphcut_labeling',
            self.path,
            Settings.path('automatic_polycube') / 'graphcut_labeling', 
            '{mesh}', # arguments template
            'graphcut_labeling_%d',
            ['labeling'],
            mesh        = str(self.get_file(self.FILENAMES.SURFACE_MESH_OBJ,    True)),
            labeling    = labeling.FILENAMES.SURFACE_LABELING_TXT
        )
    
    def evocube(self):
        # Instead of asking for the path of the output labeling, the executable wants the path to a folder where to write all output files.
        # But we dont know the output folder name given by GenerativeAlgorithm a priori (depend on the datetime) -> use a tmp output folder, then move its content into the folder created by GenerativeAlgorithm
        tmp_folder = Path(mkdtemp()) # request an os-specific tmp folder
        # evocube also wants the surface map, as 'tris_to_tets.txt', inside the output folder, but without the 'triangles' and 'tetrahedra' annotations
        with open(self.get_file(self.FILENAMES.SURFACE_MAP_TXT),'r') as infile:
            with open(tmp_folder / 'tris_to_tets.txt','w') as outfile: # where evocube expects the surface map
                for line in infile.readlines():
                    outfile.write(line.split()[0] + '\n') # keep only what is before ' '
        output_folder = GenerativeAlgorithm(
            'evocube',
            self.path,
            Settings.path('evocube'),
            '{surface_mesh} {output_folder}',
            'evocube_%d',
            [],
            surface_mesh    = str(self.get_file(self.FILENAMES.SURFACE_MESH_OBJ,    True)),
            output_folder   = str(tmp_folder.absolute())
        )
        for outfile in tmp_folder.iterdir():
            move(
                str(outfile.absolute()),
                str(output_folder.absolute())
            )
        rmtree(tmp_folder)
        # rename some files having hard-coded names in evocube
        if (output_folder / 'logs.json').exists():
            move(
                str((output_folder / 'logs.json').absolute()),
                str((output_folder / 'evocube.logs.json').absolute())
            )
        if (output_folder / 'labeling.txt').exists():
            move(
                str((output_folder / 'labeling.txt').absolute()),
                str((output_folder / labeling.FILENAMES.SURFACE_LABELING_TXT).absolute())
            )
        if (output_folder / 'labeling_init.txt').exists():
            move(
                str((output_folder / 'labeling_init.txt').absolute()),
                str((output_folder / 'initial_surface_labeling.txt').absolute())
            )
        if (output_folder / 'labeling_on_tets.txt').exists():
            move(
                str((output_folder / 'labeling_on_tets.txt').absolute()),
                str((output_folder / labeling.FILENAMES.VOLUME_LABELING_TXT).absolute())
            )
        if (output_folder / 'fast_polycube_surf.obj').exists():
            move(
                str((output_folder / 'fast_polycube_surf.obj').absolute()),
                str((output_folder / labeling.FILENAMES.POLYCUBE_SURFACE_MESH_OBJ).absolute())
            )
        # remove the tris_to_tets file created before the GenerativeAlgorithm
        if (output_folder / 'tris_to_tets.txt').exists():
            unlink(output_folder / 'tris_to_tets.txt')
        return output_folder

    def automatic_polycube(self):
        InteractiveGenerativeAlgorithm(
            'automatic_polycube',
            self.path,
            Settings.path('automatic_polycube') / 'automatic_polycube',
            '{surface_mesh}',
            'automatic_polycube_%d',
            ['labeling'],
            surface_mesh = str(self.get_file(self.FILENAMES.SURFACE_MESH_OBJ,   True)),
            labeling     = labeling.FILENAMES.SURFACE_LABELING_TXT
        )
    
    def HexBox(self):
        InteractiveGenerativeAlgorithm(
            'HexBox',
            self.path,
            Settings.path('HexBox'), 
            '{mesh}', # arguments template
            'HexBox_%d',
            ['labeling'],
            mesh        = str(self.get_file(self.FILENAMES.SURFACE_MESH_OBJ,    True)),
            labeling    = labeling.FILENAMES.SURFACE_LABELING_TXT
        )

    def AlgoHex(self):
        return GenerativeAlgorithm(
            'AlgoHex',
            self.path,
            Settings.path('AlgoHex'),
            '-i {tet_mesh} -o {hex_mesh}',
            'AlgoHex',
            ['hex_mesh'],
            tet_mesh    = str(self.get_file(self.FILENAMES.TET_MESH_VTK,    True)),
            hex_mesh    = hex_mesh.FILENAMES.HEX_MESH_OVM
        )
    
    def gridgenerator(self, scale):
        return GenerativeAlgorithm(
            'marchinghex_gridgenerator',
            self.path,
            Settings.path('marchinghex') / 'gridgenerator',
            '{input_mesh} {output_grid_mesh} {scale}',
            'marchinghex_{scale}',
            ['output_grid_mesh'],
            input_mesh    = str(self.get_file(self.FILENAMES.TET_MESH_MEDIT,True)),
            output_grid_mesh    = marchinghex_grid.FILENAMES.GRID_MESH_MEDIT,
            scale = scale
        )

    def marchinghex(self, scale, keep_debug_files = False):
        # TODO if a marchinghex_grid subfolder of the same scale exists, just execute marchinghex_hexmeshing()
        subfolder = self.gridgenerator(scale)
        grid_data_subfolder = AbstractDataFolder.instantiate(subfolder)
        grid_data_subfolder.marchinghex_hexmeshing(keep_debug_files)
        return subfolder

class marchinghex_grid(AbstractDataFolder):
    """
    Interface to the intermediate step of the marchinghex algorithm (regular grid)
    """

    class FILENAMES(SimpleNamespace):
        GRID_MESH_MEDIT = 'grid.mesh' # regular hex mesh of the bounding box, in the GMF/MEDIT ASCII format

    DEFAULT_VIEW = 'grid'

    @staticmethod
    def is_instance(path: Path) -> bool:
        return (path / marchinghex_grid.FILENAMES.GRID_MESH_MEDIT).exists() and not  (path / hex_mesh.FILENAMES.HEX_MESH_MEDIT).exists()

    def __init__(self,path: Path):
        AbstractDataFolder.__init__(self,Path(path))
    
    def view(self, what = None):
        if what == None:
            what = self.DEFAULT_VIEW
        if what == 'grid':
            InteractiveGenerativeAlgorithm(
                'view',
                self.path,
                Settings.path('Graphite'),
                '{grid_mesh}', # arguments template
                None,
                [],
                grid_mesh = str(self.get_file(self.FILENAMES.GRID_MESH_MEDIT,True))
            )
        else:
            raise Exception(f'marchinghex_grid.view() does not recognize \'what\' value: \'{what}\'')
    
    def get_file(self, filename : str, must_exist : bool = False) -> Path:
        path = super().get_file(filename)
        if (not must_exist) or (must_exist and path.exists()):
            return path
        # so 'file' is missing
        raise Exception(f'Missing file {str(path)}')
    
    # ----- Transformative algorithms (modify current folder) --------------------

    def marchinghex_hexmeshing(self, keep_debug_files):
        # note: will transform the folder type from marchinghex_grid to hex_mesh
        parent = AbstractDataFolder.instantiate(self.path.parent) # we need the parent folder to get the surface mesh
        assert(parent.type() == 'tet_mesh') # the parent folder should be of tet_mesh type
        TransformativeAlgorithm(
            'marchinghex_hexmeshing',
            self.path,
            Settings.path('marchinghex') / 'marchinghex_hexmeshing',
            '{grid_mesh} {tet_mesh} {hex_mesh}',
            grid_mesh   = str(self.get_file(self.FILENAMES.GRID_MESH_MEDIT,         True)),
            tet_mesh    = str(parent.get_file(tet_mesh.FILENAMES.TET_MESH_MEDIT,    True)),
            hex_mesh    = str(self.path / hex_mesh.FILENAMES.HEX_MESH_MEDIT)
        )
        # it may be interesting to read the last printed line to have the average Hausdorff distance between the domain and the hex-mesh
        # the executable also writes debug files
        for debug_filename in [
            'dist_hex_mesh.mesh',
            'dist_hex_sampling.geogram',
            'dist_tet_mesh.mesh',
            'dist_tet_sampling.geogram',
            'mh_result.mesh'
        ] + [x for x in Path(curdir).iterdir() if x.is_file() and x.stem.startswith('iter_')]: # and all 'iter_*' files
            if Path(debug_filename).exists():
                if keep_debug_files:
                    move(debug_filename, self.path / ('marchinghex_hexmeshing.' + str(debug_filename)))
                else:
                    unlink(debug_filename)

class labeling(AbstractDataFolder):
    """
    Interface to a labeling folder
    """

    class FILENAMES(SimpleNamespace):
        SURFACE_LABELING_TXT            = 'surface_labeling.txt'                # per-surface-triangle labels, values from 0 to 5 -> {+X,-X,+Y,-Y,+Z,-Z}
        VOLUME_LABELING_TXT             = 'volume_labeling.txt'                 # per-tet-facets labels, same values + "-1" for "no label"
        POLYCUBE_SURFACE_MESH_OBJ       = 'fastbndpolycube.obj'                 # polycube deformation of the surface mesh, in the Wavefront format
        PREPROCESSED_TET_MESH_MEDIT     = 'preprocessed.tet.mesh'               # tet-mesh with additional cells to avoid impossible configuration regarding the labeling. GMF/MEDIT ASCII format. Output of https://github.com/fprotais/preprocess_polycube
        TET_MESH_REMESHED_MEDIT         = 'tet.remeshed.mesh'                   # tet-mesh aiming bijectivity for the polycube. GMF/MEDIT ASCII format. Output of https://github.com/fprotais/robustPolycube
        TET_MESH_REMESHED_LABELING_TXT  = 'tet.remeshed.volume_labeling.txt'    # volume labeling of remeshed_tet_mesh. Should be the same as volume_labeling.
        POLYCUBOID_MESH_MEDIT           = 'polycuboid.mesh'                     # polycuboid generated from remeshed_tet_mesh and its labeling. GMF/MEDIT ASCII format.

    DEFAULT_VIEW = 'labeled_surface'

    @staticmethod
    def is_instance(path: Path) -> bool:
        return (path / labeling.FILENAMES.SURFACE_LABELING_TXT).exists() # path is an instance of labeling if it has a surface_labeling.txt file

    def __init__(self,path: Path):
        AbstractDataFolder.__init__(self,Path(path))
    
    def view(self,what = 'labeled_surface'):
        """
        View labeling with labeling_viewer app from automatic_polycube repo
        """
        parent = AbstractDataFolder.instantiate(self.path.parent) # we need the parent folder to get the surface mesh
        assert(parent.type() == 'tet_mesh') # the parent folder should be of tet-mesh type
        if what == None:
            what = self.DEFAULT_VIEW
        if what == 'labeled_surface':
            InteractiveGenerativeAlgorithm(
                'view',
                self.path,
                Settings.path('automatic_polycube') / 'labeling_viewer',
                '{surface_mesh} {surface_labeling}', # arguments template
                None,
                [],
                surface_mesh        = str(parent.get_file(tet_mesh.FILENAMES.SURFACE_MESH_OBJ,  True)),
                surface_labeling    = str(self.get_file(self.FILENAMES.SURFACE_LABELING_TXT,    True))
            )
        elif what == 'fastbndpolycube':
            InteractiveGenerativeAlgorithm(
                'view',
                self.path,
                Settings.path('automatic_polycube') / 'labeling_viewer', 
                '{surface_mesh} {surface_labeling}', # arguments template
                None,
                [],
                surface_mesh        = str(self.get_file(self.FILENAMES.POLYCUBE_SURFACE_MESH_OBJ,   True)), # surface polycube mesh instead of original surface mesh
                surface_labeling    = str(self.get_file(self.FILENAMES.SURFACE_LABELING_TXT,        True))
            )
        elif what == 'preprocessed_polycube':
            InteractiveGenerativeAlgorithm(
                'view',
                self.path,
                Settings.path('Graphite'),
                '{mesh}', # arguments template
                None,
                [],
                mesh = str(self.get_file(self.FILENAMES.PREPROCESSED_TET_MESH_MEDIT,    True))
            )
        else:
            raise Exception(f'labeling.view() does not recognize \'what\' value: \'{what}\'')
    
    def get_file(self, filename : str, must_exist : bool = False) -> Path:
        path = super().get_file(filename)
        if (not must_exist) or (must_exist and path.exists()):
            return path
        # so 'file' is missing -> try to auto-compute it
        if filename == self.FILENAMES.VOLUME_LABELING_TXT:
            self.volume_labeling()
            return self.get_file(filename,True)
        elif filename == self.FILENAMES.POLYCUBE_SURFACE_MESH_OBJ:
            self.fastbndpolycube()
            return self.get_file(filename,True)
        elif filename == self.FILENAMES.PREPROCESSED_TET_MESH_MEDIT:
            self.preprocess_polycube()
            return self.get_file(filename,True)
        elif filename in [self.FILENAMES.TET_MESH_REMESHED_MEDIT, self.FILENAMES.TET_MESH_REMESHED_LABELING_TXT, self.FILENAMES.POLYCUBOID_MESH_MEDIT]:
            self.rb_generate_deformation()
            return self.get_file(filename,True)
        raise Exception(f'Missing file {str(path)}')
        
    # ----- Transformative algorithms (modify current folder) --------------------
        
    def volume_labeling(self):
        parent = AbstractDataFolder.instantiate(self.path.parent) # we need the parent folder to get the surface map
        assert(parent.type() == 'tet_mesh') # the parent folder should be of tet_mesh type
        TransformativeAlgorithm(
            'volume_labeling',
            self.path,
            Settings.path('automatic_polycube') / 'volume_labeling', 
            '{surface_labeling} {surface_map} {tetra_labeling}',
            surface_labeling    = str(self.get_file(self.FILENAMES.SURFACE_LABELING_TXT,    True)),
            surface_map         = str(parent.get_file(tet_mesh.FILENAMES.SURFACE_MAP_TXT,   True)),
            tetra_labeling      = str(self.get_file(self.FILENAMES.VOLUME_LABELING_TXT          ))
        )

    def fastbndpolycube(self, keep_debug_files = False):
        parent = AbstractDataFolder.instantiate(self.path.parent) # we need the parent folder to get the surface mesh
        assert(parent.type() == 'tet_mesh') # the parent folder should be of tet_mesh type
        TransformativeAlgorithm(
            'fastbndpolycube',
            self.path,
            Settings.path('fastbndpolycube'),
            '{surface_mesh} {surface_labeling} {polycube_mesh}',
            surface_mesh        = str(parent.get_file(tet_mesh.FILENAMES.SURFACE_MESH_OBJ,      True)),
            surface_labeling    = str(self.get_file(self.FILENAMES.SURFACE_LABELING_TXT,        True)),
            polycube_mesh       = str(self.get_file(self.FILENAMES.POLYCUBE_SURFACE_MESH_OBJ        ))
        )
        # the fastbndpolycube executable also writes a 'flagging.geogram' file, in the current folder
        if Path('flagging.geogram').exists():
            if keep_debug_files:
                move('flagging.geogram', self.path / 'fastbndpolycube.flagging.geogram')
            else:
                unlink('flagging.geogram')

    def preprocess_polycube(self):
        """
        Edit a tetrahedral mesh, pre-processing a polycube by avoiding some configurations

        https://github.com/fprotais/preprocess_polycube

        Not really needed, see issue [#1](https://github.com/fprotais/preprocess_polycube/issues/1)
        """
        parent = AbstractDataFolder.instantiate(self.path.parent) # we need the parent folder to get the tet mesh
        assert(parent.type() == 'tet_mesh') # the parent folder should be of tet_mesh type
        TransformativeAlgorithm(
            'preprocess_polycube',
            self.path,
            Settings.path('preprocess_polycube'),
            '{init_tet_mesh} {preprocessed_tet_mesh} {volume_labeling}',
            init_tet_mesh         = str(parent.get_file(tet_mesh.FILENAMES.TET_MESH_MEDIT,      True)),
            preprocessed_tet_mesh = str(self.get_file(self.FILENAMES.PREPROCESSED_TET_MESH_MEDIT    )),
            volume_labeling         = str(self.get_file(self.FILENAMES.VOLUME_LABELING_TXT,     True))
        )

    def polycube_withHexEx(self, scale, keep_debug_files = False):
        parent = AbstractDataFolder.instantiate(self.path.parent) # we need the parent folder to get the tet mesh
        assert(parent.type() == 'tet_mesh') # the parent folder should be of tet_mesh type
        subfolder = GenerativeAlgorithm(
            'polycube_withHexEx',
            self.path,
            Settings.path('polycube_withHexEx'),
            '{tet_mesh} {volume_labeling} {hex_mesh} {scale}',
            'polycube_withHexEx_{scale}',
            ['hex_mesh'],
            tet_mesh        = str(parent.get_file(tet_mesh.FILENAMES.TET_MESH_MEDIT,    True)),
            volume_labeling = str(self.get_file(self.FILENAMES.VOLUME_LABELING_TXT,     True)),
            hex_mesh        = hex_mesh.FILENAMES.HEX_MESH_MEDIT,
            scale           = scale # scaling factor applied before libHexEx. higher = more hexahedra
        )
        # the executable also writes 2 debug .geogram files
        for debug_filename in ['Param.geogram', 'Polycube.geogram']:
            if Path(debug_filename).exists():
                if keep_debug_files:
                    move(debug_filename, self.path / ('polycube_withHexEx.' + debug_filename))
                else:
                    unlink(debug_filename)
        return subfolder
    
    def rb_generate_deformation(self, keep_debug_files = False):
        """
        https://github.com/fprotais/robustPolycube#rb_generate_deformation
        """
        if( self.get_file(self.FILENAMES.TET_MESH_REMESHED_MEDIT).exists() and 
            self.get_file(self.FILENAMES.TET_MESH_REMESHED_LABELING_TXT).exists() and 
            self.get_file(self.FILENAMES.POLYCUBOID_MESH_MEDIT).exists() ):
            # output files already exist, no need to re-run
            return
        parent = AbstractDataFolder.instantiate(self.path.parent) # we need the parent folder to get the surface map
        assert(parent.type() == 'tet_mesh') # the parent folder should be of tet_mesh type
        TransformativeAlgorithm(
            'rb_generate_deformation',
            self.path,
            Settings.path('robustPolycube') / 'rb_generate_deformation',
            '{tet_mesh} {volume_labeling} {tet_remeshed} {tet_remeshed_labeling} {polycuboid}',
            tet_mesh                = str(parent.get_file(tet_mesh.FILENAMES.TET_MESH_MEDIT,        True)),
            volume_labeling         = str(self.get_file(self.FILENAMES.VOLUME_LABELING_TXT,         True)),
            tet_remeshed            = str(self.get_file(self.FILENAMES.TET_MESH_REMESHED_MEDIT          )),
            tet_remeshed_labeling   = str(self.get_file(self.FILENAMES.TET_MESH_REMESHED_LABELING_TXT   )),
            polycuboid              = str(self.get_file(self.FILENAMES.POLYCUBOID_MESH_MEDIT            ))
        )
        # the executable also writes debug .geogram files
        for debug_filename in [
            'debug_volume_0.geogram',
            'debug_flagging_1.geogram',
            'debug_volume_flagging_2.geogram',
            'debug_embedded_mesh_3.geogram',
            'debug_corrected_polycuboid_4.geogram',
            'debug__wflagging_5.geogram',
            'debug_corrected_param_6.geogram'
        ]:
            if Path(debug_filename).exists():
                if keep_debug_files:
                    move(debug_filename, self.path / ('rb_generate_deformation.' + debug_filename))
                else:
                    unlink(debug_filename)
    
    def rb_generate_quantization(self,element_sizing, keep_debug_files = False):
        """
        https://github.com/fprotais/robustPolycube#rb_generate_quantization
        """
        subfolder = GenerativeAlgorithm(
            'rb_generate_quantization',
            self.path,
            Settings.path('robustPolycube') / 'rb_generate_quantization',
            '{tet_remeshed} {tet_remeshed_labeling} {polycuboid} {element_sizing} {hex_mesh}',
            'robustPolycube_{element_sizing}',
            ['hex_mesh'],
            tet_remeshed            = str(self.get_file(self.FILENAMES.TET_MESH_REMESHED_MEDIT,         True)),
            tet_remeshed_labeling   = str(self.get_file(self.FILENAMES.TET_MESH_REMESHED_LABELING_TXT,  True)),
            polycuboid              = str(self.get_file(self.FILENAMES.POLYCUBOID_MESH_MEDIT,           True)),
            element_sizing          = element_sizing, # ratio compared to tet_remeshed edge size. smaller = more hexahedra
            hex_mesh                = hex_mesh.FILENAMES.HEX_MESH_MEDIT
        )
        # the executable also writes debug .geogram files and a .lua script
        for debug_filename in [
            'debug_volume_0.geogram',
            'debug_polycuboid_1.geogram',
            'debug_flagging_2.geogram',
            'debug_corrected_flagging_3.geogram',
            'debug_charts_dim_0__4.geogram',
            'debug_charts_dim_1__5.geogram',
            'debug_charts_dim_2__6.geogram',
            'debug_Blocks_on_mesh_7.geogram',
            'debug_Blocks_blocks_8.geogram',
            'debug_Blocks_on_polycuboid_9.geogram',
            'debug_Blocks_on_polycube_10.geogram',
            'debug_coarsehexmesh_11.geogram',
            'debug_coarsehexmesh_charts_12.geogram',
            'debug_polycubehexmesh_13.geogram',
            'debug_polycubehexmesh_charts_14.geogram',
            'debug_hexmesh_15.geogram',
            'debug_hexmesh_charts_16.geogram',
            'view.lua'
        ]:
            if Path(debug_filename).exists():
                if keep_debug_files:
                    move(debug_filename, subfolder / ('rb_generate_quantization.' + str(debug_filename)))
                else:
                    unlink(debug_filename)
        return subfolder

class hex_mesh(AbstractDataFolder):
    """
    Interface to a hex mesh data subfolder
    """

    class FILENAMES(SimpleNamespace):
        HEX_MESH_MEDIT  = 'hex.mesh'        # hexahedral mesh, GMF/MEDIT ASCII format
        HEX_MESH_OVM    = 'hex_mesh.ovm'    # hexahedral mesh, OpenVolumeMesh format

    DEFAULT_VIEW = 'hex_mesh'

    @staticmethod
    def is_instance(path: Path) -> bool:
        return (path / hex_mesh.FILENAMES.HEX_MESH_MEDIT).exists() or (path / hex_mesh.FILENAMES.HEX_MESH_OVM).exists()

    def __init__(self,path: Path):
        AbstractDataFolder.__init__(self,Path(path))
    
    def view(self, what = None):
        """
        View hex-mesh (MEDIT format) with hex_mesh_viewer from automatic_polycube
        """
        if what == None:
            what = self.DEFAULT_VIEW
        if what == 'hex_mesh':
            InteractiveGenerativeAlgorithm(
                'view',
                self.path,
                Settings.path('automatic_polycube') / 'hex_mesh_viewer',
                '{mesh}', # arguments template
                None,
                [],
                mesh = str(self.get_file(self.FILENAMES.HEX_MESH_MEDIT, True))
            )
        else:
            raise Exception(f'hex_mesh.view() does not recognize \'what\' value: \'{what}\'')
    
    def get_file(self, filename : str, must_exist : bool = False) -> Path:
        path = super().get_file(filename)
        if (not must_exist) or (must_exist and path.exists()):
            return path
        # so 'file' is missing -> try to auto-compute it
        if filename == self.FILENAMES.HEX_MESH_MEDIT:
            self.OVM_to_MEDIT()
            return self.get_file(filename,True)
        raise Exception(f'Missing file {str(path)}')

    # ----- Transformative algorithms (modify current folder) --------------------

    def OVM_to_MEDIT(self):
        TransformativeAlgorithm(
            'OVM_to_MEDIT',
            self.path,
            Settings.path('ovm.io'),
            '{input} {output}',
            input   = str(self.get_file(self.FILENAMES.HEX_MESH_OVM,    True)),
            output  = str(self.get_file(self.FILENAMES.HEX_MESH_MEDIT       )),
        )

class root(AbstractDataFolder):
    """
    Interface to the root folder of the database
    """

    class FILENAMES(SimpleNamespace):
        COLLECTIONS = 'collections.json' # store (nested) lists of subfolders, for batch execution. JSON format

    DEFAULT_VIEW = 'print_path'

    @staticmethod
    def is_instance(path: Path) -> bool:
        return (path / root.FILENAMES.COLLECTIONS).exists()
    
    def __init__(self,path: Path = Settings.path('data_folder')):
        assert(path == Settings.path('data_folder')) # only accept instanciation of the folder given by the settings file. 2nd argument required by abstract class
        if not path.exists(): # if the data folder does not exist
            logging.warning(f'Data folder {str(path)} does not exist and will be created')
            # create the data folder
            mkdir(path) # TODO manage failure case
            # write empty collections file
            with open(path / root.FILENAMES.COLLECTIONS,'w') as file:
                dump(dict(), file, sort_keys=True, indent=4)
        self.collections_manager = CollectionsManager(path)
        AbstractDataFolder.__init__(self,path)
    
    def view(self, what = None):
        if what == None:
            what = self.DEFAULT_VIEW
        if what == 'print_path':
            print(str(self.path.absolute()))
        else:
            raise Exception(f'root.view() does not recognize \'what\' value: \'{what}\'')
        
    def get_file(self, filename : str, must_exist : bool = False) -> Path:
        path = super().get_file(filename)
        if (not must_exist) or (must_exist and path.exists()):
            return path
        raise Exception(f'Missing file {str(path)}')

    def recursive_update(self):
        # for each filename, list old filenames
        old_filenames = dict()
        # 2023-09-30:
        old_filenames[tet_mesh.FILENAMES.TET_MESH_MEDIT]                = [ 'tetra.mesh' ]
        old_filenames[tet_mesh.FILENAMES.TET_MESH_VTK]                  = [ 'tet.vtk' ]
        old_filenames[labeling.FILENAMES.VOLUME_LABELING_TXT]           = [ 'tetra_labeling.txt' ]
        old_filenames[labeling.FILENAMES.PREPROCESSED_TET_MESH_MEDIT]   = [ 'preprocessed.tetra.mesh' ]
        old_filenames[hex_mesh.FILENAMES.HEX_MESH_OVM]                  = [ 'hex.ovm' ]
        # rename all occurencies of old filenames
        count = 0
        for subdir in [x for x in self.path.rglob('*') if x.is_dir()]: # recursive exploration of all folders
            for new, olds in old_filenames.items(): # for each entry in old_filenames (corresponding to a new filename)
                for old in olds : # for each old filename of new
                    if (subdir / old).exists(): # if, inside the current subdir, there is a file with an old filename
                        rename_file(subdir, old, new) # rename it and register the modification in the JSON file
                        count += 1
        logging.info(f'root.recursive_update() : {count} modifications')
        
    # ----- Generative algorithms (create subfolders) --------------------

    def import_MAMBO(self,path_to_MAMBO : str = None):
        tmp_dir_used = True
        if path_to_MAMBO==None:
            if not UserInput.ask("No input was given, so the MAMBO dataset will be downloaded, are you sure you want to continue ?"):
                logging.info("Operation cancelled")
                exit(0)
            url = 'https://gitlab.com/franck.ledoux/mambo/-/archive/master/mambo-master.zip'
            tmp_folder = Path(mkdtemp()) # request an os-specific tmp folder
            zip_file = tmp_folder / 'mambo-master.zip'
            path_to_MAMBO = tmp_folder / 'mambo-master'
            logging.info('Downloading MAMBO')
            request.urlretrieve(url=url,filename=str(zip_file))
            logging.info('Extracting archive')
            unpack_archive(zip_file,extract_dir=tmp_folder)
        else:
            tmp_dir_used = False
            path_to_MAMBO = Path(path_to_MAMBO).absolute()
            logging.info('MAMBO will be imported from folder ' + str(path_to_MAMBO))
            if not path_to_MAMBO.exists():
                logging.fatal(str(path_to_MAMBO) + ' does not exist')
                exit(1)
            if not path_to_MAMBO.is_dir():
                logging.fatal(str(path_to_MAMBO) + ' is not a folder')
                exit(1)
        for subfolder in [x for x in path_to_MAMBO.iterdir() if x.is_dir()]:
            if subfolder.name in ['Scripts', '.git']:
                continue # ignore this subfolder
            for file in [x for x in subfolder.iterdir() if x.suffix == '.step']:
                step_object = step(self.path / file.stem,file)
                print(file.stem + ' imported')
                self.collections_manager.append_to_collection('MAMBO_'+subfolder.name,str(file.stem)) # 'MAMBO_Basic', 'MAMBO_Simple' & 'MAMBO_Medium' collections
            self.collections_manager.append_to_collection('MAMBO','MAMBO_'+subfolder.name) # 'MAMBO' collection, will contain 'MAMBO_Basic', 'MAMBO_Simple' & 'MAMBO_Medium'
        self.collections_manager.save()

        if tmp_dir_used:
            # delete the temporary directory
            logging.debug('Deleting folder \'' + str(tmp_folder) + '\'')
            rmtree(tmp_folder)