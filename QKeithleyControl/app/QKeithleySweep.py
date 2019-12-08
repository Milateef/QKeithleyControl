# ---------------------------------------------------------------------------------
# 	QKeithleyControl
# 	Copyright (C) 2019 Michael Winters
#	mwchalmers@protonmail.com
# ---------------------------------------------------------------------------------
# 
# 	Permission is hereby granted, free of charge, to any person obtaining a copy
# 	of this software and associated documentation files (the "Software"), to deal
# 	in the Software without restriction, including without limitation the rights
# 	to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# 	copies of the Software, and to permit persons to whom the Software is
# 	furnished to do so, subject to the following conditions:
# 	
# 	The above copyright notice and this permission notice shall be included in all
# 	copies or substantial portions of the Software.
# 	
# 	THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# 	IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# 	FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# 	AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# 	LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# 	OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# 	SOFTWARE.
#

#!/usr/bin/env python 
import os
import sys
import time
import hashlib
import threading

# Import visa and numpy
import visa
import numpy as np

# Import custom widgets
import widgets.QVisaApplication
import widgets.QUnitSelector
import widgets.QDynamicPlot 


# Import QT backends
from PyQt5.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QMessageBox, QComboBox, QSpinBox, QDoubleSpinBox, QPushButton, QCheckBox, QLabel, QLineEdit, QStackedWidget
from PyQt5.QtCore import Qt, QStateMachine, QState, QObject
from PyQt5.QtCore import Qt, QStateMachine, QState, QObject
from PyQt5.QtGui import QIcon

# Container class to construct sweep measurement widget
class QKeithleySweep(widgets.QVisaApplication.QVisaApplication):

	def __init__(self, _config):

		# Inherits QVisaApplication -> QWidget
		super(QKeithleySweep, self).__init__(_config)

		# Generate Main Layout
		self.gen_main_layout()


	#####################################
	# WIDGET HELPER METHODS
	#

	# Wrapper method to get keitley write handle
	# 	Returns the pyVisaDevice object
	def keithley(self):
		return self._config._get_inst_byname(self.inst_select.currentText())

	# Method to reset sweep on window switch
	def refresh(self):
	
		# If add insturments have been initialized
		if self._config._get_inst_handles() is not None:

			# Reset the widget
			self.inst_select.clear()
			self.inst_select.addItems(self._config._get_inst_names())

			# Update sweep parameters and enable output button
			self.meas_button.setEnabled(True)
			self.update_sweep_params()			

		else: 

			# Disable output button
			self.meas_button.setEnabled(False)	

	# Method to set sweep parameters
	def set_sweep_params(self, start, stop, npts):
		
		# No hysteresis	
		if self.sweep_hist.currentText() == "None": 	
			sp = np.linspace(float(start), float(stop), int(npts) )
			self._set_meta( "sweep", sp)

		# Prepare reverse sweep
		if self.sweep_hist.currentText() == "Reverse-sweep":

			# Sweep centered hysteresis
			sp = np.linspace(float(start), float(stop), int(npts) )
			sp = np.concatenate( (sp, sp[-2::-1]) )
			self._set_meta( "sweep", sp)

		# Prepare a zero centered hysteresis
		if self.sweep_hist.currentText() == "Zero-centered":

			# Create a linspace
			sp = np.linspace(float(start), float(stop), int(npts) )
			
			# Extract positive slice
			pos = np.where(sp > 0, sp, np.nan) 	
			pos = pos[~np.isnan(pos)]

			# Extract negative slice
			neg = np.where(sp < 0, sp, np.nan) 
			neg = neg[~np.isnan(neg)]

			# Create the zero centered hysteresis re-insert zeros
			# Forward sweep, zero crossing
			if (start < 0.) and (stop > 0.) and (start < stop):
				sp = np.concatenate( ([0.0], pos, pos[-2::-1], [0.0], neg[::-1], neg[1::], [0.0]) )

		 	# Reverse sweep, zero crossing
			elif  (start > 0.) and (stop < 0.) and (start > stop):	
				sp = np.concatenate( ([0.0], neg[::-1], neg[1::], [0.0], pos, pos[-2::-1], [0.0]) )

			# If not zero crossing, default to "Reverse-sweep" case
			else: 	
				sp = np.concatenate( (sp, sp[-2::-1]) )	

			# Set meta field
			self._set_meta( "sweep", sp)


	# Method to get sweep parameters
	def get_sweep_params(self):
		return self._get_meta("sweep")	


	#####################################
	# SWEEP MODE MAIN LAYOUTS
	#
	# *) gem_main_layout()
	# 	1) gen_sweep_ctrl()
	# 		a) gen_voltage_sweep()
	#		b) gen_current_sweep()
	#	2) gen_sweep_plot()
	#

	def gen_main_layout(self):
	
		# Create Icon for QMessageBox
		self.icon = QIcon(os.path.join(os.path.dirname(os.path.realpath(__file__)), "python.ico"))	
		
		# Create layout objects and set layout
		self.layout = QHBoxLayout()
		self.layout.addLayout(self.gen_sweep_ctrl())
		self.layout.addWidget(self.gen_sweep_plot())
		self.setLayout(self.layout)


	# Sweep control layout
	def gen_sweep_ctrl(self): 

		self.ctl_layout = QVBoxLayout()

		#####################################
		#  SWEEP STATE MACHINE AND BUTTON
		#

		# Measurement Button. This will be a state machine which 
		# alternates between 'measure' and 'abort' states
		self.meas_state  = QStateMachine()
		self.meas_button = QPushButton()

		self.meas_button.setStyleSheet(
			"background-color: #dddddd; border-style: solid; border-width: 1px; border-color: #aaaaaa; padding: 7px;" )

		# Create measurement states
		self.meas_run  = QState()
		self.meas_stop = QState()

		# Assign state properties and transitions
		self.meas_run.assignProperty(self.meas_button, 'text', 'Abort Sweep')
		self.meas_run.addTransition(self.meas_button.clicked, self.meas_stop)
		self.meas_run.entered.connect(self.exec_sweep_run)

		self.meas_stop.assignProperty(self.meas_button, 'text', 'Measure Sweep')
		self.meas_stop.addTransition(self.meas_button.clicked, self.meas_run)
		self.meas_stop.entered.connect(self.exec_sweep_stop)

		# Add states, set initial state, and state machine
		self.meas_state.addState(self.meas_run)
		self.meas_state.addState(self.meas_stop)
		self.meas_state.setInitialState(self.meas_stop)
		self.meas_state.start()

		# Add insturement selector
		self.inst_select_label = QLabel("Select Device")
		self.inst_select = QComboBox()
		self.inst_select.setFixedWidth(200)	


		#####################################
		#  SWEEP MEASUREMENT CONFIGURATION
		#
		
		# Current/Voltage Sweep Mode 
		self.sweep_select_label = QLabel("Sweep Mode")
		self.sweep_select = QComboBox()
		self.sweep_select.setFixedWidth(200)
		self.sweep_select.addItems(["Voltage", "Current"])	
		self.sweep_select.currentTextChanged.connect(self.update_sweep_ctrl)

		# Generate voltage and current source widgets
		self.gen_voltage_sweep()		# self.voltage_sweep
		self.gen_current_sweep()		# self.current_sweep

		# Add to stacked widget
		self.sweep_pages = QStackedWidget()	
		self.sweep_pages.addWidget(self.voltage_sweep)
		self.sweep_pages.addWidget(self.current_sweep)
		self.sweep_pages.setCurrentIndex(0)

		# Hysteresis mode
		self.sweep_hist_label = QLabel("Hysteresis Mode")
		self.sweep_hist = QComboBox()
		self.sweep_hist.setFixedWidth(200)
		self.sweep_hist.addItems(["None", "Reverse-sweep", "Zero-centered"])	

		#####################################
		#  ADD CONTROLS
		#

		# Measurement Button
		self.ctl_layout.addWidget(self.meas_button)

		# Sweep configuration controls
		self.ctl_layout.addWidget(self._gen_hbox_widget([self.inst_select, self.inst_select_label]))
		self.ctl_layout.addWidget(self._gen_hbox_widget([self.sweep_select, self.sweep_select_label]))
		self.ctl_layout.addWidget(self._gen_hbox_widget([self.sweep_hist, self.sweep_hist_label]))
		self.ctl_layout.addWidget(self.sweep_pages)

		# Spacer and save widget
		self.ctl_layout.addStretch(1)
		self.ctl_layout.addWidget(self._gen_save_widget())
	
		# Positioning
		self.ctl_layout.setContentsMargins(0,15,0,20)
		return self.ctl_layout

	# Generate voltage and current sweep widgets
	def gen_voltage_sweep(self):
	
		# New QWidget
		self.voltage_sweep = QWidget()
		self.voltage_layout = QVBoxLayout()
	
		# Sweep Start
		self.voltage_start_config={
			"unit" 		: "V",
			"min"		: "u",
			"max"		: "",
			"label"		: "Sweep Start (V)",
			"limit"		: 20.0,
			"signed"	: True,
			"default"	: [-1.0, ""]
		} 
		self.voltage_start = widgets.QUnitSelector.QUnitSelector(self.voltage_start_config)

		# Sweep Stop
		self.voltage_stop_config={
			"unit" 		: "V",
			"min"		: "u",
			"max"		: "",
			"label"		: "Sweep Start (V)",
			"limit"		: 20.0,
			"signed"	: True,
			"default"	: [1.0, ""]
		} 
		self.voltage_stop = widgets.QUnitSelector.QUnitSelector(self.voltage_stop_config)

		# Compliance Spinbox
		self.voltage_cmpl_config={
			"unit" 		: "A", 
			"min"		: "u",
			"max"		: "",
			"label"		: "Compliance (A)",
			"limit"		: 1.0, 
			"signed"	: False,
			"default"	: [100, "m"]
		} 
		self.voltage_cmpl = widgets.QUnitSelector.QUnitSelector(self.voltage_cmpl_config)	

		# Number of points
		self.voltage_npts_config={
			"unit" 		: "__INT__", 
			"label"		: "Number of Points",
			"limit"		: 256.0, 
			"signed"	: False,
			"default"	: [11.0]
		}
		self.voltage_npts = widgets.QUnitSelector.QUnitSelector(self.voltage_npts_config)

		# Measurement Delay
		self.voltage_delay_config={
			"unit" 		: "__DOUBLE__", 
			"label"		: "Measurement Interval (s)",
			"limit"		: 60.0, 
			"signed"	: False,
			"default"	: [0.1]
		}
		self.voltage_delay = widgets.QUnitSelector.QUnitSelector(self.voltage_delay_config)

		# Pack selectors into layout
		self.voltage_layout.addWidget(self.voltage_start)
		self.voltage_layout.addWidget(self.voltage_stop)
		self.voltage_layout.addWidget(self.voltage_cmpl)
		self.voltage_layout.addWidget(self.voltage_npts)
		self.voltage_layout.addWidget(self.voltage_delay)
		self.voltage_layout.setContentsMargins(0,0,0,0)

		# Set layout 
		self.voltage_sweep.setLayout(self.voltage_layout)	


	def gen_current_sweep(self):
	
		# New QWidget
		self.current_sweep = QWidget()
		self.current_layout = QVBoxLayout()
	
		# Sweep Start
		self.current_start_config={
			"unit" 		: "A",
			"min"		: "u",
			"max"		: "",
			"label"		: "Sweep Start (A)",
			"limit"		: 1.0,
			"signed"	: True,
			"default"	: [0.0, "m"]
		} 
		self.current_start = widgets.QUnitSelector.QUnitSelector(self.current_start_config)

		# Sweep Stop
		self.current_stop_config={
			"unit" 		: "A",
			"min"		: "u",
			"max"		: "",
			"label"		: "Sweep Stop (A)",
			"limit"		: 1.0,
			"signed"	: True,
			"default"	: [100, "m"]
		} 
		self.current_stop = widgets.QUnitSelector.QUnitSelector(self.current_stop_config)

		# Compliance Spinbox
		self.current_cmpl_config={
			"unit" 		: "V", 
			"min"		: "u",
			"max"		: "",
			"label"		: "Compliance (V)",
			"limit"		: 20, 
			"signed"	: False,
			"default"	: [1.0, ""]
		} 
		self.current_cmpl = widgets.QUnitSelector.QUnitSelector(self.current_cmpl_config)

		# Number of points
		self.current_npts_config={
			"unit" 		: "__INT__", 
			"label"		: "Number of Points",
			"limit"		: 256.0, 
			"signed"	: False,
			"default"	: [11.0]
		}
		self.current_npts = widgets.QUnitSelector.QUnitSelector(self.current_npts_config)

		# Measurement Delay
		self.current_delay_config={
			"unit" 		: "__DOUBLE__", 
			"label"		: "Measurement Interval (s)",
			"limit"		: 60.0, 
			"signed"	: False,
			"default"	: [0.1]
		}
		self.current_delay = widgets.QUnitSelector.QUnitSelector(self.current_delay_config)			

		# Pack selectors into layout
		self.current_layout.addWidget(self.current_start)
		self.current_layout.addWidget(self.current_stop)
		self.current_layout.addWidget(self.current_cmpl)
		self.current_layout.addWidget(self.current_npts)
		self.current_layout.addWidget(self.current_delay)
		self.current_layout.setContentsMargins(0,0,0,0)

		# Set layout 
		self.current_sweep.setLayout(self.current_layout)	


	# Dynamic Plotting Capability
	def gen_sweep_plot(self): 		

		# Create QDynamicPlot object (inherits QWidget) 
		self.plot = widgets.QDynamicPlot.QDynamicPlot()
		self.plot.set_axes_labels("Voltage (V)", "Current (A)")
		self.plot.add_axes()

		# Connect clear plot button to update_sweep_ctrl
		self.plot.refresh.clicked.connect(self.update_sweep_ctrl)

		# Return the plot
		return self.plot		

	#####################################
	#  SWEEP CONTROL UPDATE METHODS
	#	

	# Sweep control dynamic update
	def update_sweep_ctrl(self):

		# Enforce data/plot consistency
		self.plot.refresh_axes()
		if self.plot.hlist == []:
			self._reset_data()

		# Switch to voltage sweep page
		if self.sweep_select.currentText() == "Voltage":
			self.sweep_pages.setCurrentIndex(0)
			self.update_sweep_params()

		# Switch to current sweep page
		if self.sweep_select.currentText() == "Current":		
			self.sweep_pages.setCurrentIndex(1)
			self.update_sweep_params()

	# Create Measurement 
	def update_sweep_params(self):

		# Set up v-source(i-compliance) on keithley 
		if self.sweep_select.currentText() == "Voltage":
			
			self.keithley().voltage_src()
			self.keithley().set_voltage(0.0)
			self.keithley().current_cmp(self.voltage_cmpl.value())

			# Set sweeep paramaters
			self.set_sweep_params(
				self.voltage_start.value(), 
				self.voltage_stop.value(), 
				self.voltage_npts.value())


		# Set up i-source(v-compliance) on keithley 
		if self.sweep_select.currentText() == "Current":

			self.keithley().current_src()
			self.keithley().set_current(0.0)
			self.keithley().voltage_cmp(self.current_cmpl.value())

			# Set sweeep paramaters
			self.set_sweep_params(
				self.current_start.value(), 
				self.current_stop.value(), 
				self.current_npts.value())


	#####################################
	#  MEASUREMENT EXECUTION THREADS
	#		

	# Execute Sweep Measurement
	def exec_sweep_thread(self):

		# Create a unique data key
		m = hashlib.sha256()
		m.update(str("sweep@%s"%str(time.time())).encode() )		
		m.hexdigest()[:7]

		# Measurement key
		_meas_key = "sweep %s"%m.hexdigest()[:6]

		# Add to data
		self._add_meas_key(_meas_key)
		self._set_data_fields(_meas_key, ["t", "V", "I", "P"])

		# Generate function pointer for voltage/current mode
		if self.sweep_select.currentText() == "Voltage":
			__func__  = self.keithley().set_voltage
			__delay__ = self.voltage_delay.value()

		if self.sweep_select.currentText() == "Current":
			__func__ = self.keithley().set_current
			__delay__ = self.current_delay.value()

		# Clear plot and zero arrays
		handle = self.plot.add_handle()
		start  = time.time()
		
		# Output on
		self.keithley().output_on()

		# Loop through sweep variables
		for _bias in self.get_sweep_params():

			# If thread is running
			if self.thread_running:

				# Set voltage/current bias
				__func__(_bias)			

				# Get data from buffer
				_buffer = self.keithley().meas().split(",")
		
				if __delay__ != 0: 
					time.sleep(__delay__)

				# Extract data from buffer
				self._data[_meas_key]["t"].append( float( time.time() - start ) )
				self._data[_meas_key]["V"].append( float(_buffer[0]) )
				self._data[_meas_key]["I"].append( float(_buffer[1]) )
				self._data[_meas_key]["P"].append( float(_buffer[0]) * float(_buffer[1]) )

				self.plot.update_handle(handle, float(_buffer[0]), float(_buffer[1]))
				self.plot._draw_canvas()	
		
		# Reset Keithley
		__func__(0.0)
		self.keithley().output_off()
		
		# Reset sweep control and update measurement state to stop. 
		# Post a button click event to the QStateMachine to trigger 
		# a state transition if thread is still running (not aborted)
		if self.thread_running:
			self.meas_button.click()

	# Function we run when we enter run state
	def exec_sweep_run(self):

		self.update_sweep_params()

		# For startup protection
		if self.keithley() is not None:

			# Update UI button to abort 
			self.meas_button.setStyleSheet(
				"background-color: #ffcccc; border-style: solid; border-width: 1px; border-color: #800000; padding: 7px;")
			self._enable_save(False)

	 		# Run the measurement thread function
			self.thread = threading.Thread(target=self.exec_sweep_thread, args=())
			self.thread.daemon = True						# Daemonize thread
			self.thread.start()         					# Start the execution
			self.thread_running = True

		# Otherwise show infobox and revert state
		else:
			self.meas_button.click()
			msg = QMessageBox()
			msg.setIcon(QMessageBox.Warning)
			msg.setText("Keithley not configured")
			msg.setWindowTitle("Sweep Info")
			msg.setWindowIcon(self.icon)
			msg.setStandardButtons(QMessageBox.Ok)
			msg.exec_()

	# Function we run when we enter abort state
	def exec_sweep_stop(self):
	
		# For startup protection
		if self.keithley() is not None:

			# Update UI button to start state
			self.meas_button.setStyleSheet(
				"background-color: #dddddd; border-style: solid; border-width: 1px; border-color: #aaaaaa; padding: 7px;" )
			self._enable_save(True)

			# Kill measurement thread
			self.thread_running = False
			self.thread.join()  # Waits for thread to complete
